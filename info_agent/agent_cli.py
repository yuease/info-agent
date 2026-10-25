"""Non-interactive JSON CLI over the same Python core; no web service is required."""
import argparse
import json
import sys
from pathlib import Path
from info_agent.agent import AgentCore,CoreError
from info_agent.workflow import ROOT


class Parser(argparse.ArgumentParser):
    def error(self,message):
        # argparse's original message can echo a private question/value.
        self.exit(2,json.dumps(dict(error_code='INVALID_ARGUMENT',error_message='参数无效，请使用 --help 查看静态用法。'),ensure_ascii=False)+'\n')


def main(argv=None,*,core_factory=AgentCore):
    parser=Parser(description='本地档案 Agent 查询核心，返回 JSON；仅 query 可调用模型。')
    parser.add_argument('--root',type=Path,default=ROOT,help='宿主指定项目目录，不应作为模型工具参数')
    parser.add_argument('--profile',type=Path,help='宿主指定 root 内的配置文件')
    sub=parser.add_subparsers(dest='command',required=True)
    for name in ('search','query','item','sources','preferences','update','undo'):
        cmd=sub.add_parser(name,description='query 必须由宿主提供 --term 核心关键词并保留原 question；不从问句生成关键词、时间或来源。今天需显式 --today。' if name=='query' else None)
        cmd.add_argument('--request-file',type=Path,help='读取完整请求 JSON，避免问题出现在命令行')
        cmd.add_argument('--profile-id',help='必须与宿主绑定配置一致；省略时使用该配置 ID')
        if name in ('search','query'):
            if name=='query':cmd.add_argument('question',nargs='?',help='完整原问题，仅用于筛选理解；不能代替 --term')
            cmd.add_argument('--term',action='append',default=[],help='核心关键词，可重复传入；query 必需至少一个，否则 QUERY_TERMS_REQUIRED；search 可省略浏览')
            cmd.add_argument('--start');cmd.add_argument('--end');cmd.add_argument('--today',action='store_true')
            cmd.add_argument('--timezone',default='Asia/Shanghai');cmd.add_argument('--source',action='append',default=[])
            cmd.add_argument('--content-type',action='append',default=[]);cmd.add_argument('--limit',type=int,default=8 if name=='query' else 20)
            cmd.add_argument('--page',type=int,default=1);cmd.add_argument('--snapshot-version');cmd.add_argument('--request-id')
            cmd.add_argument('--collect-temporary',action='store_true');cmd.add_argument('--temporary-keyword',action='append',default=[])
            cmd.add_argument('--timeout',type=float);cmd.add_argument('--max-duration',type=int)
        elif name=='item':cmd.add_argument('item_id',nargs='?');cmd.add_argument('--max-body-chars',type=int,default=4000)
        elif name=='sources':cmd.add_argument('--include-disabled',action='store_true')
        elif name=='update':
            cmd.add_argument('--expected-version');cmd.add_argument('--request-id')
            cmd.add_argument('--add-keyword',action='append',default=[]);cmd.add_argument('--remove-keyword',action='append',default=[])
            cmd.add_argument('--interests');cmd.add_argument('--interest-mode',choices=['same_goal','switch_goal'])
        elif name=='undo':cmd.add_argument('change_id',nargs='?')
    args=parser.parse_args(argv)
    try:
        path=args.profile
        if path is not None and not path.is_absolute():path=args.root/path
        core=core_factory(args.root,profile_path=path)
        if args.request_file:
            payload=json.loads(args.request_file.read_text(encoding='utf-8-sig'))
        else:
            payload={'profile_id':args.profile_id or core.profile_id}
            if args.command in ('search','query'):
                sources=[]
                for value in args.source:
                    kind,_,key=value.partition(':');sources.append(dict(kind=kind,key=key or None))
                payload.update(terms=args.term,time_range=dict(start=args.start,end=args.end,period='today' if args.today else None,timezone=args.timezone),
                    sources=sources,content_types=args.content_type,limit=args.limit,page=args.page,snapshot_version=args.snapshot_version,
                    request_id=args.request_id,collection_mode='temporary_keywords' if args.collect_temporary else 'existing_only',
                    temporary_keywords=args.temporary_keyword,timeout_seconds=args.timeout,max_duration_seconds=args.max_duration)
                if args.command=='query':payload['question']=args.question
            elif args.command=='item':payload.update(item_id=args.item_id,max_body_chars=args.max_body_chars)
            elif args.command=='sources':payload['include_disabled']=args.include_disabled
            elif args.command=='update':payload.update(expected_version=args.expected_version,request_id=args.request_id,add_keywords=args.add_keyword,
                remove_keywords=args.remove_keyword,interests=args.interests,interest_mode=args.interest_mode)
            elif args.command=='undo':payload['change_id']=args.change_id
        method={'search':'search_items','query':'query_updates','item':'get_item','sources':'get_source_status',
            'preferences':'get_preferences','update':'update_preferences','undo':'undo_preference_change'}[args.command]
        result=getattr(core,method)(payload)
        print(result.model_dump_json(indent=2))
        return 1 if getattr(result,'outcome',None) in ('model_failed','collection_failed') else 0
    except CoreError as exc:
        print(json.dumps(exc.as_dict(),ensure_ascii=False),file=sys.stderr);return 2
    except Exception:
        print(json.dumps(dict(error_code='QUERY_FAILED',error_message='操作未完成，请检查本地文件或参数；原始诊断未输出。'),ensure_ascii=False),file=sys.stderr);return 2


if __name__=='__main__':raise SystemExit(main())
