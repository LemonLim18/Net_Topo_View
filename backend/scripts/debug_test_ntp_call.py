#!/usr/bin/env python3
"""Debug helper: call common_services.test_ntp() directly and print traceback/result."""
import asyncio
import os
import sys

_THIS = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_THIS)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from common_services import api


async def main():
    try:
        res = await api.test_ntp()
        print('RESULT:', res)
    except Exception as e:
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    asyncio.run(main())
