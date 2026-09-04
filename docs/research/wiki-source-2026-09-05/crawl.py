import urllib.request, urllib.parse, re, json, hashlib, concurrent.futures, pathlib
from html.parser import HTMLParser
ROOT=pathlib.Path(__file__).parent
BASE='https://www.tower-hub.com'
class Reader(HTMLParser):
 def __init__(self): super().__init__(); self.parts=[];self.links=[];self.headers=[];self.skip=0;self.head=False;self.h=[];self.table=0;self.prose=[]
 def handle_starttag(self,tag,attrs):
  a=dict(attrs)
  if tag in ('script','style'):self.skip+=1
  if tag=='table':self.table+=1
  if tag in ('h1','h2','h3','h4'):self.head=True;self.h=[]
  if tag=='a' and a.get('href'):self.links.append(a['href'])
  if tag in ('p','li','tr','h1','h2','h3','h4','dt','dd'): self.parts.append('\n');self.prose.append('\n')
 def handle_endtag(self,tag):
  if tag in ('script','style'):self.skip=max(0,self.skip-1)
  if tag=='table':self.table=max(0,self.table-1)
  if tag in ('h1','h2','h3','h4'):self.head=False;self.headers.append(' '.join(self.h))
  if tag in ('p','li','tr','h1','h2','h3','h4','dt','dd'): self.parts.append('\n');self.prose.append('\n')
 def handle_data(self,data):
  if self.skip:return
  t=re.sub(r'\s+',' ',data).strip()
  if t:self.parts.append(t+' ')
  if t and not self.table:self.prose.append(t+' ')
  if self.head:self.h.append(t)
def fetch(path):
 url=BASE+path
 try:
  resp=urllib.request.urlopen(url,timeout=30); raw=resp.read(); html=raw.decode(); r=Reader();
  frag=re.search(r'<article\b.*?</article>',html,re.S) or re.search(r'<main\b.*?</main>',html,re.S)
  r.feed(frag.group() if frag else html)
  allr=Reader();allr.feed(html)
  title=re.search(r'<title>(.*?)</title>',html,re.S).group(1)
  clean=lambda p:'\n'.join(x.strip() for x in ''.join(p).splitlines() if x.strip())
  name=path.strip('/').replace('/','__') or 'home'
  (ROOT/(name+'.txt')).write_text(clean(r.parts))
  (ROOT/(name+'.prose.txt')).write_text(clean(r.prose))
  return {'url':url,'path':path,'status':resp.status,'title':title,'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),'chars':len(clean(r.parts)),'headings':r.headers,'text_file':name+'.txt','prose_file':name+'.prose.txt','links':sorted(set(allr.links))}
 except Exception as e:return {'url':url,'path':path,'error':str(e)}
seen=set();pending={'/wiki','/wiki/guide','/glossary'}; rows=[]
while pending:
 batch=sorted(pending-seen); pending=set()
 if not batch:break
 seen.update(batch)
 with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
  for row in pool.map(fetch,batch):
   rows.append(row)
   for link in row.get('links',[]):
    parsed=urllib.parse.urlparse(urllib.parse.urljoin(BASE,link))
    if parsed.netloc in ('www.tower-hub.com','tower-hub.com') and (parsed.path=='/wiki' or parsed.path.startswith('/wiki/')) and parsed.path not in seen:pending.add(parsed.path)
 print('Fetched',len(rows),'next',len(pending),flush=True)
(ROOT/'manifest.json').write_text(json.dumps({'retrieved':'2026-09-05','scope':'All recursively linked English /wiki pages plus /glossary','sources':rows},indent=2))
print('DONE',len(rows),'errors',sum('error' in r for r in rows),'characters',sum(r.get('chars',0) for r in rows))
