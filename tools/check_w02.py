"""Read-only W2 precheck; exit zero means ready for review, never course pass."""
import argparse
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "platform"))
from fde_product.w02_natural_checker import precheck
from fde_product.interview_extraction import Workspace, CheckError


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--workspace', default=str(ROOT))
    p.add_argument('--run', action='append', help='采用的知识对照 run.json；可重复，省略时只查默认目录下一层。')
    p.add_argument('--config', default='customer/w02/knowledge.json')
    p.add_argument('--w1-fit-gap', default='artifacts/w01/product_fit_gap.json')
    p.add_argument('--w2-fit-gap')
    p.add_argument('--output', help='可选新报告路径，相对于客户仓库；不覆盖已有文件，已存在时结果仍会显示但不保存。')
    a = p.parse_args()
    try:
        result = precheck(a.workspace, knowledge_config=a.config, runs=a.run,
                          w1_fit_gap=a.w1_fit_gap, w2_fit_gap=a.w2_fit_gap)
        # Show the result before saving, so an existing report path never discards a finished precheck.
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        if a.output:
            try:
                Workspace(a.workspace).write(a.output, result)
            except CheckError as e:
                print(json.dumps({'status':'error', 'code':e.code, 'path':e.location,
                    'message':'本次预检结果已在标准输出显示，但没有保存：报告文件已存在，不覆盖旧报告。请换一个新路径重新运行。'
                    if e.code == 'OUTPUT_EXISTS' else '本次预检结果已在标准输出显示，但报告没有保存。'}, ensure_ascii=False), file=sys.stderr)
                return 2
        return int(result['status'] != 'ready_for_review')
    except CheckError as e:
        print(json.dumps({'status':'error', 'code':e.code, 'path':e.location}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
