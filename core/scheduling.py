"""
This is a general purpose scheduler. It does best effort scheduling and
execution of expired items in the order they are added. This also means that
there is no guarantee the tasks will be executed on time every time, in fact
they will always be late, even if just by milliseconds. If you need it to be
done on time, you schedule it early, but remember that it will still be best
effort.

The way this scheduler is supposed to be used is to add a scheduling queue,
then you can add tasks to the queue to either be put in a task queue ASAP, or
at or an absolute time in the future. The queue should be consumed by a worker
thread.

This module defines the following objects:

 - :class:`core.scheduling.ScheduledTaskContext`
    A context that wraps around any data you want to pass to the scheduler and
    which will be added to the task queue when the schedule time expires.
 - :class:`core.scheduling.SchedulerThread`
    An object that is capable of scheduling and unscheduling tasks that you
    can define with, you should add contexts to the schedule with an optional
    time. The context should to have a proper ``__repr__()`` defined since the
    scheduler relies on it to be a unique identifier.
"""
import threading
import logging
import datetime
import queue
import time

LOG = logging.getLogger()


class ScheduledTaskContext(object):
    """
    A context for scheduled tasks, this context can be updated with an
    exception count per exception type, so it can be re-scheduled if it is
    the appropriate action.
    """
    def __init__(self, task, sched_time=None, name=None, **attributes):
        """
        Initialise a ScheduledTaskContext with task data, optional scheduled
        time and optional name.

        :param str task: A task corresponding to an existing queue in the
            target scheduler.
        :param datetime.datetime|int sched_time: Absolute time
            (datetime.datetime object) or relative time in seconds (int) to
            execute the task.
        :param str name: A name for the context instance (used in
            ``__repr__()``)
        :param kwargs attributes: Any data you want to assign to the context,
            avoid using names already defined in the context: scheduler, task,
            name, sched_time, reschedule.
        """
        # this will be set when it is passed to a scheduler automatically.
        self.scheduler = None
        self.task = task
        self.name = name
        self.sched_time = sched_time
        for attr, value in attributes.items():
            if hasattr(self, attr):
                raise AttributeError(
                    "Can't set \"{}\" it's a reserved attribute name.".format(
                        attr)
                )
            self.__setattr__(attr, value)

    def reschedule(self, sched_time=None):
        """
        Reschedule this context itself.

        :param datetime.datetime sched_time: When should this context be added
            back to the task queue
        """
        try:
            self.scheduler.add_task(self, sched_time)
        except AttributeError:
            raise AttributeError(
                "This context was never added to a queue before.")

    def __repr__(self):
        if self.name:
            return "<ScheduledTaskContext {}: {}>".format(
                self.task, self.name)
        else:
            return "<ScheduledTaskContext {}>".format(self.task)


class SchedulerThread(threading.Thread):
    """
    This object can be used to schedule tasks for contexts.

    The context can be whatever you define as long as the ``__repr__()`` will
    return something that is unique among your contexts. When the scheduled
    time has *passed*, the context will be added back to the task queue,
    where it can be consumed by a worker thread. When a task is scheduled you
    can choose to have it added to the task queue ASAP or at a specified
    absolute point in time. If you add it at a time in the past, it will be
    added to the task queue the first time the scheduler checks expired
    tasks schedule times.
    """
    def __init__(self, *args, **kwargs):
        """
        Initialise the thread's arguments and its parent
        :class:`threading.Thread`.

        :kwarg iterable queues: A list, tuple or any iterable that returns
            strings that should be the names of queues.
        :raises KeyError: If the queue name is already taken (only when queues
            kwarg is used).
        """
        self._queues = {}

        # Keeping the tasks both in normal and reverse order to allow quick
        # unscheduling
        # The schedule contains items indexed by time
        self.schedule = {}
        # The scheduled are a list of tasks indexed by file name
        self.scheduled = {}

        queues = kwargs.pop('queues', None)
        if queues:
            for _queue in queues:
                self.add_queue(_queue)

        super(SchedulerThread, self).__init__(*args, **kwargs)

    def add_queue(self, task, max_size=0):
        """
        Add a scheduled queue to the scheduler.

        :param str task: A unique name that is used by worker threads.
        :param int max_size: Maximum queue depth, default=0 (unlimited).
        :raises KeyError: If the queue name is already taken..
        """
        if task in self._queues:
            raise KeyError("This queue is already taken.")
        self._queues[task] = queue.Queue(max_size)

    def add_task(self, ctx):
        """
        Add a task to be executed either ASAP, or at a specific time.
        Set ``sched_time`` to ``None`` if you want your task executed ASAP, set
        it to a :obj:`datetime.datetime` object if you want to schedule it.

        If the context is not unique, the scheduled task will be cancelled
        before scheduling the new task.

        :param ScheduledTaskContext ctx: A context containing data for a
            worker thread.
        :raises Queue.Full: If the underlying task queue is full.
        """
        ctx.scheduler = self
        if not ctx.sched_time:
            # Run scheduled tasks ASAP by adding it to the queue.
            return self._queue_task(ctx)

        sched_time = ctx.sched_time
        if isinstance(sched_time, int):
            # Convert relative time in seconds to absolute time
            sched_time = datetime.datetime.now() + \
                datetime.timedelta(seconds=sched_time)

        if ctx in self.scheduled:
            LOG.warning("Task %s was already scheduled, unscheduling.", ctx)
            self.cancel_task(ctx)
        # Run scheduled tasks after ctx.sched_time seconds.
        self.scheduled[ctx] = sched_time
        if ctx.sched_time in self.schedule:
            self.schedule[sched_time].append(ctx)
        else:
            self.schedule[sched_time] = [ctx]
        LOG.info(
            "Scheduled %s at %s",
            ctx, ctx.sched_time.strftime('%Y-%m-%d %H:%M:%S'))

    def _queue_task(self, ctx):
        try:
            self._queues[ctx.task].put(ctx)
        except KeyError as key:
            raise KeyError("Queue for task {} might not exist.", key)

    def cancel_task(self, ctx):
        """
        Remove a task from the queue.

        :param ScheduledTaskContext ctx: A context containing data for a
            worker thread.
        :return bool: True for successfully cancelled task or False.
        """
        try:
            # Find out when it was scheduled
            sched_time = self.scheduled.pop(ctx)
            # There can be more than one task scheduled in the same time
            # slot so we need to filter out any value that is not our target
            # and leave it
            slot = self.schedule[sched_time]
            slot[:] = [x for x in slot if x is ctx]
            return True
        except KeyError:
            LOG.warning("Can't unschedule, %s wasn't scheduled.", ctx)
            return False

    def get_task(self, task, blocking=True, timeout=None):
        """
        Get a task context from the task queue ``task``.

        :param str task: Task name that refers to a scheduler queue.
        :param bool blocking: Wait until there is something to return from the
            queue.
        :raises Queue.Empty: If the underlying task queue is empty and
            blocking is False or the timout expires.
        :raises KeyError: If the task does not exist.
        """
        try:
            return self._queues[task].get(blocking, timeout)
        except KeyError:
            raise KeyError("Queue \"{}\" does not exist.".format(task))

    def task_done(self, task):
        """
        Mark a task done on a queue, this up the queue's counter of completed
        tasks.

        :param str task: The task queue name.
        """
        try:
            return self._queues[task].task_done()
        except KeyError:
            raise KeyError("Queue \"{}\" does not exist.".format(task))

    def run(self):
        """
        Start the certificate finder thread.
        """
        LOG.info("Started a scheduler thread.")
        while True:
            self._run()
            time.sleep(1)

    def run_all(self):
        """
        Run all tasks currently queued regardless schedule time.
        """
        self._run(True)

    def _run(self, all_tasks=False):
        """
        Runs all scheduled tasks that have a scheduled time < now.
        """
        now = datetime.datetime.now()
        # Take a copy of all sched_time keys
        if all_tasks:
            todo = list(self.schedule)
        else:
            # Only scheduled before or at now, default
            todo = [x for x in self.schedule if x <= now]
        for sched_time in todo:
            items = self.schedule.pop(sched_time)
            for ctx in items:
                LOG.info("Adding %s to the %s queue.", ctx, ctx.task)
                # Remove from reverse indexed dict
                del self.scheduled[ctx]

                self._queue_task(ctx)
                late = datetime.datetime.now() - sched_time
                if late.seconds < 1:
                    late = ''
                elif 1 < late.seconds < 59:  # between 1 and 59 seconds
                    late = " {} seconds late".format(late.seconds)
                else:
                    late = " {} late".format(
                        late.strftime('%H:%M:%S')
                    )
                LOG.debug(
                    "Queued %s at %s%s",
                    ctx, now.strftime('%Y-%m-%d %H:%M:%S'), late)
