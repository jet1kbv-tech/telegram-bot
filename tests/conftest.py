import asyncio
import inspect


def pytest_pyfunc_call(pyfuncitem):
    """Run coroutine tests without adding a production/test runtime dependency."""
    if not inspect.iscoroutinefunction(pyfuncitem.obj):
        return None
    signature = inspect.signature(pyfuncitem.obj)
    kwargs = {name: pyfuncitem.funcargs[name] for name in signature.parameters}
    asyncio.run(pyfuncitem.obj(**kwargs))
    return True
