"""在编辑器中直接运行本文件，生成并在控制台显示简报。"""
from info_agent.workflow import main


if __name__ == "__main__":
    print("开始获取 RSS 并生成简报，请稍候……", flush=True)
    raise SystemExit(main(["--show"]))
