import os,json,time,subprocess,hashlib,unicodedata
from pathlib import Path
import sys,argparse
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from social_read.images import LocalOCR
from social_read.rapidocr_worker import recognize
os.umask(0o077)
parser=argparse.ArgumentParser();parser.add_argument('--manifest',default='.state/ocr-benchmark/samples.json');args=parser.parse_args()
manifest=Path(args.manifest);root=manifest.parent; samples=json.loads(manifest.read_text());binary=LocalOCR(root).binary(None)
def normal(s):return ''.join(unicodedata.normalize('NFC',s).split())
def distance(a,b):
 if not a:return len(b)
 if not b:return len(a)
 if a==b:return 0
 # Myers bit-vector Levenshtein, arbitrary-size Python integers.
 masks={}
 for i,ch in enumerate(a):masks[ch]=masks.get(ch,0)|(1<<i)
 mask=(1<<len(a))-1;last=1<<(len(a)-1);positive=mask;negative=0;score=len(a)
 for ch in b:
  equal=masks.get(ch,0);vertical=equal|negative
  horizontal=(((equal&positive)+positive)^positive)|equal
  plus=negative|~(horizontal|positive);minus=positive&horizontal
  if plus&last:score+=1
  if minus&last:score-=1
  plus=(plus<<1)|1;minus<<=1
  positive=(minus|~(vertical|plus))&mask;negative=(plus&vertical)&mask
 return score
rows=[]
for sample in samples:
 for backend in ['vision','rapid-default','rapid-tiled']:
  begin=time.monotonic()
  if backend=='vision':value=json.loads(subprocess.check_output([str(binary),sample['path']],timeout=60))
  else:value=recognize(sample['path'],tiled=backend=='rapid-tiled')
  elapsed=time.monotonic()-begin;text='\n'.join(x['text'] for x in value.get('lines',[]));truth=sample.get('truth')
  row={**{k:v for k,v in sample.items() if k not in ['truth','path']},'backend':backend,'elapsed_seconds':round(elapsed,3),'text_chars':len(text),'status':value['status'],'tiles':value.get('tiles',1),'cer':round(distance(normal(truth),normal(text))/len(normal(truth)),4) if truth else None,'image_sha256':hashlib.sha256(Path(sample['path']).read_bytes()).hexdigest()}
  rows.append(row);(root/(sample['id']+'-'+backend+'.json')).write_text(json.dumps({'metrics':row,'text':text,'detail':value},ensure_ascii=False))
  (root/'metrics.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2));print(json.dumps(row,ensure_ascii=False),flush=True)
