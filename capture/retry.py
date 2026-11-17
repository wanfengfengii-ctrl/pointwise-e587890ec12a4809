from dataclasses import dataclass


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3

    def __post_init__(self):
        if type(self.max_attempts) is not int or self.max_attempts < 1:
            raise ValueError("positive attempt budget required")

    def after_source_failure(self, attempts):
        return "retryable" if attempts < self.max_attempts else "failed"

    def after_interpretation_failure(self, attempts):
        return "failed"

    def after_interrupted_run(self, attempts):
        return self.after_source_failure(attempts)
