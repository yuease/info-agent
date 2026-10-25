"""Default is offline retained-data search; --query explicitly permits the configured model."""
import argparse
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from info_agent.agent import AgentCore,CoreError

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--query',action='store_true',help='有候选且未缓存时调用模型；默认关闭')
    args=parser.parse_args()
    core=AgentCore(args.root)
    # Updates: query_updates; locating one work: search_items; credits/details: get_item.
    # If query is insufficient but search located the item, get_item can read retained
    # body without network/model calls. Query itself also includes base body evidence.
    # Host supplies core words and time; keep the full question for model understanding.
    request=dict(profile_id=core.profile_id,terms=['Codex','重置'],time_range={'period':'today'},request_id='example-v4-codex-reset-001',limit=4)
    try:
        if args.query:request['question']='今天有没有 Codex 重置相关消息'
        result=(core.query_updates if args.query else core.search_items)(request)
        print(result.model_dump_json(indent=2))
        return 0
    except CoreError as error:
        print(error.as_dict());return 2

if __name__=='__main__':raise SystemExit(main())
