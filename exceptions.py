class ConfigError(Exception):
    pass

class InstrumentError(Exception):
    pass

class OrderError(Exception):
    pass

class LegRiskError(Exception):
    def __init__(self, message, entry_legs=None, exit_legs=None):
        super().__init__(message)
        self.entry_legs = entry_legs or []
        self.exit_legs = exit_legs or []