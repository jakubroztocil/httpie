from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from httpie.context import Environment

if TYPE_CHECKING:
    from rich.console import Console


@dataclass
class BaseDisplay:
    env: Environment

    def start(
        self, *, total: Optional[float], at: float, description: str
    ) -> None:
        ...

    def update(self, steps: float) -> None:
        ...

    def stop(self, time_spent: float) -> None:
        ...

    @property
    def console(self) -> 'Console':
        """Returns the default console to be used with displays (stderr)."""
        return self.env.rich_error_console

    def _print_summary(
        self, is_finished: bool, observed_steps: int, time_spent: float
    ):
        from rich import filesize

        if is_finished:
            verb = 'Done'
        else:
            verb = 'Interrupted'

        total_size = filesize.decimal(observed_steps)
        avg_speed = filesize.decimal(observed_steps / time_spent)

        minutes, seconds = divmod(time_spent, 60)
        hours, minutes = divmod(int(minutes), 60)
        if hours:
            total_time = f'{hours:d}:{minutes:02d}:{seconds:0.5f}'
        else:
            total_time = f'{minutes:02d}:{seconds:0.5f}'

        self.console.print(
            f'[progress.description]{verb}. {total_size} in {total_time} ({avg_speed}/s)'
        )


class DummyDisplay(BaseDisplay):
    """
    A dummy display object to be used when the progress bars,
    spinners etc. are disabled globally (or during tests).
    """


class StatusDisplay(BaseDisplay):
    def start(
        self, *, total: Optional[float], at: float, description: str
    ) -> None:
        self.observed = at
        self.description = (
            f'[progress.description]{description}[/progress.description]'
        )

        self.status = self.console.status(self.description, spinner='line')
        self.status.start()

    def update(self, steps: float) -> None:
        from rich import filesize

        self.observed += steps

        observed_amount, observed_unit = filesize.decimal(
            self.observed
        ).split()
        self.status.update(
            status=f'{self.description} [progress.download]{observed_amount}/? {observed_unit}[/progress.download]'
        )

    def stop(self, time_spent: float) -> None:
        self.status.stop()
        self.console.print(self.description)
        if time_spent:
            self._print_summary(
                is_finished=True,
                observed_steps=self.observed,
                time_spent=time_spent,
            )


class ProgressDisplay(BaseDisplay):
    def start(
        self, *, total: Optional[float], at: float, description: str
    ) -> None:
        from rich.progress import (
            Progress,
            BarColumn,
            DownloadColumn,
            TimeRemainingColumn,
            TransferSpeedColumn,
        )
        self.resumed_at = at
        assert total is not None
        self.console.print(f'[progress.description]{description}')
        self.progress_bar = Progress(
            '[',
            BarColumn(),
            ']',
            '[progress.percentage]{task.percentage:>3.0f}%',
            '(',
            DownloadColumn(),
            ')',
            TimeRemainingColumn(),
            TransferSpeedColumn(),
            console=self.console,
            transient=True,
        )
        self.progress_bar.start()
        self.transfer_task = self.progress_bar.add_task(
            description, completed=at, total=total
        )

    def update(self, steps: float) -> None:
        self.progress_bar.advance(self.transfer_task, steps)

    def stop(self, time_spent: Optional[float]) -> None:
        self.progress_bar.stop()

        if time_spent:
            [task] = self.progress_bar.tasks
            self._print_summary(
                is_finished=task.finished,
                observed_steps=task.completed - self.resumed_at,
                time_spent=time_spent,
            )
