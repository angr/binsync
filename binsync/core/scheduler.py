import threading
import logging
from queue import PriorityQueue
from threading import Thread

l = logging.getLogger(__name__)


#
# Description Classes
#

class SchedSpeed:
    FAST = 1
    AVERAGE = 2
    SLOW = 3


class FailedJob:
    def __init__(self, reason):
        self.reason = reason

#
# Jobs
#


class Job:
    def __init__(self, function, *args, **kwargs):
        self.function = function
        self.args = args
        self.kwargs = kwargs

        self.ret_value = None
        self.finish_event = threading.Event()

    def execute(self):
        self.ret_value = self.function(*self.args, **self.kwargs)
        self.finish_event.set()


class PriorityJob:
    def __init__(self, job, priority):
        self.job = job
        self.priority = priority

    def __lt__(self, other):
        return self.priority < other.priority

    def execute(self):
        self.job.execute()


#
# Main Scheduler
#


class Scheduler:
    def __init__(self, sleep_interval=0.05):
        self.sleep_interval = sleep_interval
        self._worker = Thread(target=self._worker_thread)
        self._job_queue = PriorityQueue()
        self.lock = threading.Lock()
        self._work = False

    def stop_worker_thread(self):
        self._work = False

    def start_worker_thread(self):
        self._work = True
        self._worker.setDaemon(True)
        self._worker.start()

    def _worker_thread(self):
        while self._work:
            self._complete_a_job(block=True)

    def schedule_job(self, job: Job, priority=SchedSpeed.SLOW):
        self._job_queue.put(
            PriorityJob(job, priority)
        )

    def schedule_and_wait_job(self, job: Job, priority=SchedSpeed.SLOW, timeout=30):
        self.schedule_job(job, priority=priority)
        try:
            job.finish_event.wait(timeout=timeout)
        except Exception as e:
            return FailedJob(e)
        else:
            return job.ret_value

    def _complete_a_job(self, block=False):
        if block:
            prio_job = self._job_queue.get()
        elif not self._job_queue.empty():
            prio_job = self._job_queue.get_nowait()
        else:
            return

        prio_job.execute()
