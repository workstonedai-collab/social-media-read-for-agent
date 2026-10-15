import os,json,sqlite3,threading,hashlib
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
os.umask(0o077)
import argparse
p=argparse.ArgumentParser();p.add_argument('--output',default='.state/ocr-benchmark');p.add_argument('--font',required=True);p.add_argument('--math-font',required=True);args=p.parse_args()
root=Path(args.output);root.mkdir(parents=True,exist_ok=True)
font=args.font
texts=['用户把项目交给智能助手，自动检查环境并引导登录。','搜索结果保留来源和日期，失败后继续读取缺失图片。','小字、长图与数学公式需要分别比较，不能只看字数。','知识管理工具：飞书、思源笔记、Obsidian 与 Notion。']
samples=[]
for label,size,count,width in [('small-14',14,12,700),('small-20',20,12,900),('long-6000',22,160,1000),('long-12000',22,320,1000)]:
 height=max(500,count*37+80);im=Image.new('RGB',(width,height),'white');d=ImageDraw.Draw(im);lines=[]
 for i in range(count):
  line=f'{i+1:03d} '+texts[i%4];lines.append(line);d.text((24,30+i*37),line,font=ImageFont.truetype(font,size),fill='black')
 im.save(root/(label+'.png'));samples.append({'id':label,'kind':'long' if label.startswith('long') else 'small','path':str(root/(label+'.png')),'truth':'\n'.join(lines),'synthetic':True})
formulas=['f(x) = x² + 2x + 1','a² + b² = c²','α + β = π','∫ x² dx = x³/3 + C','x = (−b ± √(b² − 4ac)) / 2a','Σ i = n(n + 1)/2']
for sz in [22,38]:
 im=Image.new('RGB',(1000,650),'white');d=ImageDraw.Draw(im)
 for i,s in enumerate(formulas):d.text((30,30+i*90),s,font=ImageFont.truetype(args.math_font,sz),fill='black')
 key='formula-'+str(sz);im.save(root/(key+'.png'));samples.append({'id':key,'kind':'formula','path':str(root/(key+'.png')),'truth':'\n'.join(formulas),'synthetic':True,'limitation':'linear unicode formulas, not LaTeX or layout ground truth'})
(root/'samples.json').write_text(json.dumps(samples,ensure_ascii=False))
