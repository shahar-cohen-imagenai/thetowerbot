import urllib.request,re,json,pathlib,concurrent.futures,collections
P=pathlib.Path(__file__).parent;M=json.loads((P/'manifest.json').read_text());decoder=json.JSONDecoder()
def one(r):
 if r['path'].count('/')<3:return r
 s=urllib.request.urlopen(r['url'],timeout=30).read().decode();streams=[]
 for match in re.finditer(r'self\.__next_f\.push\((.*?)\)</script>',s,re.S):
  try:
   x=json.loads(match.group(1)); streams.extend(y for y in x[1:] if isinstance(y,str))
  except Exception:pass
 stream=''.join(streams);tables=[]
 for match in re.finditer(r'\{"type":"table"',stream):
  try:
   ob,_=decoder.raw_decode(stream[match.start():])
   if 'headers' in ob and 'rows' in ob and ob not in tables:tables.append(ob)
  except Exception:pass
 r['full_table_count']=len(tables);r['full_table_rows']=sum(len(t['rows']) for t in tables)
 r['verification_dates']=sorted(set(re.findall(r'Last verified.{0,30}?(20\d\d-\d\d-\d\d)',stream)))
 if tables:
  fname=r['text_file'].replace('.txt','.tables.json');(P/fname).write_text(json.dumps(tables,indent=2));r['table_file']=fname
 return r
with concurrent.futures.ThreadPoolExecutor(max_workers=6) as e:M['sources']=list(e.map(one,M['sources']))
(P/'manifest.json').write_text(json.dumps(M,indent=2)); print('Tables',sum(r.get('full_table_count',0) for r in M['sources']),'Rows',sum(r.get('full_table_rows',0) for r in M['sources']));print('Verification dates',collections.Counter(d for r in M['sources'] for d in r.get('verification_dates',[])))
