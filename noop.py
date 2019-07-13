def _noop(*args, **kwargs):
    pass


class NoOp:
    def __getattr__(self, name):
        return _noop


noOper = NoOp()
