"""Misma política de scheduling sobre transporte local o MPI real."""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import time

from .model import PlanError
from .worker import execute

TASK, RESULT, STOP = 10, 11, 12


class LocalTransport:
    def __init__(self, workers: int):
        if type(workers) is not int or workers < 1:
            raise PlanError('workers debe ser un entero positivo')
        self.workers = list(range(1, workers + 1))
        self.pool = ThreadPoolExecutor(max_workers=workers)
        self.futures = {}

    def submit(self, worker, task, arguments, server):
        future = self.pool.submit(execute, task, arguments, server)
        self.futures[future] = worker

    def receive(self):
        done, _ = wait(self.futures, return_when=FIRST_COMPLETED)
        future = next(iter(done))
        worker = self.futures.pop(future)
        try:
            result = future.result()
        except Exception as exc:
            result = {'status': 'failed', 'error': f'{type(exc).__name__}: {exc}', 'duration_seconds': 0.0}
        return worker, result

    def close(self):
        self.pool.shutdown(wait=True)


class MPITransport:
    def __init__(self, comm, MPI):
        self.comm, self.MPI = comm, MPI
        self.workers = list(range(1, comm.Get_size()))
        if not self.workers:
            raise PlanError('MPI requiere al menos 2 ranks: mpiexec -n 5 python -m midterm run ... --backend mpi')
        self.deadlines = {}

    def submit(self, worker, task, arguments, server):
        self.comm.send((task, arguments, server), dest=worker, tag=TASK)
        self.deadlines[worker] = time.monotonic() + task.timeout_seconds + 15

    def receive(self):
        status = self.MPI.Status()
        while not self.comm.Iprobe(source=self.MPI.ANY_SOURCE, tag=RESULT, status=status):
            if any(time.monotonic() > d for d in self.deadlines.values()):
                raise RuntimeError('Worker MPI sin respuesta; se abortará el job para evitar bloqueo')
            time.sleep(0.001)
        worker = status.Get_source()
        result = self.comm.recv(source=worker, tag=RESULT)
        self.deadlines.pop(worker)
        return worker, result

    def close(self):
        for worker in self.workers:
            self.comm.send(None, dest=worker, tag=STOP)


def mpi_worker_loop(comm, MPI):
    while True:
        status = MPI.Status()
        payload = comm.recv(source=0, tag=MPI.ANY_TAG, status=status)
        if status.Get_tag() == STOP:
            return
        if status.Get_tag() != TASK:
            raise RuntimeError('Mensaje MPI inesperado')
        task, arguments, server = payload
        try:
            result = execute(task, arguments, server)
        except Exception as exc:
            result = {'status': 'failed', 'error': f'{type(exc).__name__}: {exc}', 'duration_seconds': 0.0}
        comm.send(result, dest=0, tag=RESULT)
