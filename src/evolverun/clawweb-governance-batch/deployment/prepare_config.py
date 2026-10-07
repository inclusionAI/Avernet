"""Prepare this independent deployment from existing local configuration. No network/model calls."""
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parent))
from clawweb_batch.config import Config, load_config, load_llm
from clawweb_batch.artifacts import write_json


def main():
    os.umask(0o077)
    source=Path(sys.argv[1]).resolve(strict=True)
    target=ROOT/'config.json'
    if target.exists():
        raise ValueError('configuration already exists; do not silently overwrite reviewed deployment')
    original=source.read_bytes()
    raw=json.loads(original)
    data={key:value for key,value in raw.items() if key in Config.__dataclass_fields__}
    data.update(output_dir=str(ROOT/'runs'),state_dir=str(ROOT/'state'),allow_writes=False,cron_only=True)
    llm_path=Path(data['llm_config_file'])
    data['llm_config_file']=str((llm_path if llm_path.is_absolute() else source.parent/llm_path).resolve(strict=True))
    model_path=Path(data['llm_config_file'])
    model_hash=hashlib.sha256(model_path.read_bytes()).hexdigest()
    write_json(target,data)
    cfg=load_config(target)
    llm=load_llm(cfg.llm_config_file)
    unchanged=original==source.read_bytes() and model_hash==hashlib.sha256(model_path.read_bytes()).hexdigest()
    if not unchanged:
        raise ValueError('existing configuration changed during preparation')
    status={'status':'PREPARED_NOT_RUN','source_config':str(source),'source_config_unchanged':True,
            'model_config_unchanged':True,'model':llm['model'],'allow_writes':cfg.allow_writes,
            'project':cfg.project,'effect_center':cfg.clawweb_url,
            'data_queries_executed':False,'model_invoked':False,'schedule_created':False}
    write_json(ROOT/'prepared.json',status)
    print(json.dumps(status,ensure_ascii=False))


if __name__=='__main__':main()
