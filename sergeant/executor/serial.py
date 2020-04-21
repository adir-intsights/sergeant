import typing
import types
import os
import signal

from .. import killer
from .. import worker


class SerialExecutor:
    def __init__(
        self,
        worker: worker.Worker,
    ) -> None:
        self.worker = worker

        self.currently_working = False

        has_soft_timeout = self.worker.config.timeouts.soft_timeout > 0
        has_hard_timeout = self.worker.config.timeouts.hard_timeout > 0
        has_critical_timeout = self.worker.config.timeouts.critical_timeout > 0
        should_use_a_killer = has_soft_timeout or has_hard_timeout or has_critical_timeout
        if should_use_a_killer:
            self.killer = killer.process.Killer(
                pid_to_kill=os.getpid(),
                sleep_interval=0.1,
                soft_timeout=self.worker.config.timeouts.soft_timeout,
                hard_timeout=self.worker.config.timeouts.hard_timeout,
                critical_timeout=self.worker.config.timeouts.critical_timeout,
            )

            self.original_int = signal.getsignal(signal.SIGINT)
            self.original_abrt = signal.getsignal(signal.SIGABRT)
            signal.signal(signal.SIGABRT, self.sigabrt_handler)
            signal.signal(signal.SIGINT, self.sigint_handler)
        else:
            self.killer = None

    def sigabrt_handler(
        self,
        signal_num: int,
        frame: types.FrameType,
    ) -> None:
        if self.currently_working:
            raise worker.WorkerHardTimedout()

    def sigint_handler(
        self,
        signal_num: int,
        frame: types.FrameType,
    ) -> None:
        if self.currently_working:
            raise worker.WorkerSoftTimedout()

    def execute_tasks(
        self,
        tasks: typing.Iterable[typing.Dict[str, typing.Any]],
    ) -> None:
        for task in tasks:
            self.execute_task(
                task=task,
            )

    def execute_task(
        self,
        task: typing.Dict[str, typing.Any],
    ) -> None:
        try:
            self.pre_work(
                task=task,
            )

            returned_value = self.worker.work(
                task=task,
            )

            self.post_work(
                task=task,
                success=True,
            )

            self.worker._on_success(
                task=task,
                returned_value=returned_value,
            )
        except Exception as exception:
            self.post_work(
                task=task,
                success=False,
                exception=exception,
            )

            if isinstance(exception, worker.WorkerTimedout):
                self.worker._on_timeout(
                    task=task,
                )
            elif isinstance(exception, worker.WorkerRetry):
                self.worker._on_retry(
                    task=task,
                )
            elif isinstance(exception, worker.WorkerMaxRetries):
                self.worker._on_max_retries(
                    task=task,
                )
            elif isinstance(exception, worker.WorkerRequeue):
                self.worker._on_requeue(
                    task=task,
                )
            else:
                self.worker._on_failure(
                    task=task,
                    exception=exception,
                )

    def pre_work(
        self,
        task: typing.Dict[str, typing.Any],
    ) -> None:
        try:
            self.worker.pre_work(
                task=task,
            )
        except Exception as exception:
            self.worker.logger.error(
                msg=f'pre_work has failed: {exception}',
                extra={
                    'task': task,
                },
            )

        self.currently_working = True

        if self.killer:
            self.killer.start()

    def post_work(
        self,
        task: typing.Dict[str, typing.Any],
        success: bool,
        exception: typing.Optional[Exception] = None,
    ) -> None:
        if self.killer:
            self.killer.stop_and_reset()

        self.currently_working = False

        try:
            self.worker.post_work(
                task=task,
                success=success,
                exception=exception,
            )
        except Exception as exception:
            self.worker.logger.error(
                msg=f'post_work has failed: {exception}',
                extra={
                    'task': task,
                },
            )

    def __del__(
        self,
    ) -> None:
        if self.killer:
            try:
                self.killer.kill()

                signal.signal(signal.SIGABRT, self.original_abrt)
                signal.signal(signal.SIGINT, self.original_int)
            except Exception:
                pass