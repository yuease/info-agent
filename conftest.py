"""pytest 引导文件：让测试能 `import app`。

pytest 默认只把测试文件所在目录加入 sys.path，而我们的代码在项目根的 app/ 包里，
所以这里显式把项目根加进去。放在项目根还有个作用：pytest 会以它为 rootdir 起点。
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
