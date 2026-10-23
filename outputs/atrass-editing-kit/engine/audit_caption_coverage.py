#!/usr/bin/env python3
"""Triage retained ASR words absent from caption choices; never approve ASR/audio.
Overlapping ASR chunks and explicit boundary/text decisions are reported apart
from exact word selections. Matching requires both time overlap and a literal
punctuation-normalized token. A reported match is not semantic/audio approval.
"""
from pathlib import Path
import argparse,json,hashlib,re,unicodedata

def norm(s):return ''.join(c for c in unicodedata.normalize('NFC',s)if c.isalnum())
def audit(d,t):
    keeps=[x for x in d['takes']if x['action']=='keep'];selected={};captions=[]
    for c in d['captions']:
        si=c['segment_index'];s=t['segments'][si];words=s.get('words',[])
        if 'source_range'in c:a,z=c['source_range'];text=c['text']
        else:
            lo,hi=c.get('word_range',[0,len(words)])
            if words:
                assert 0<=lo<hi<=len(words);a,z=words[lo]['start'],words[hi-1]['end'];text=c.get('text',''.join(w['word']for w in words[lo:hi]).strip())
                for wi in range(lo,hi):selected.setdefault((si,wi),[]).append(c['id'])
            else:a,z=s['start'],s['end'];text=c.get('text',s['text'])
        captions.append({'id':c['id'],'segment_index':si,'source_range':[a,z],'tokens':{norm(x)for x in text.split()},'explicit_text_range':'source_range'in c})
    missing=[];reconciled=[];partial=[];whole=exact=0
    for si,s in enumerate(t['segments']):
        for wi,w in enumerate(s.get('words',[])):
            a,z=w['start'],w['end'];inside=[k for k in keeps if k['source_in']-1e-8<=a<z<=k['source_out']+1e-8];row={'segment_index':si,'word_index':wi,'word':w['word'],'source_range':[a,z]}
            if not inside:
                if z>a and any(k['source_in']<z and a<k['source_out']for k in keeps):partial.append(row)
                continue
            whole+=1
            if(si,wi)in selected:exact+=1;continue
            matches=[c for c in captions if c['source_range'][0]<z and a<c['source_range'][1]and norm(w['word'])in c['tokens']and(c['explicit_text_range']or c['segment_index']!=si)]
            if matches:reconciled.append({**row,'matches':[{k:c[k]for k in ['id','segment_index','source_range','explicit_text_range']}for c in matches]})
            else:missing.append({**row,'take_ids':[k['id']for k in inside],'segment_text':s['text']})
    return {'schema':'atrass-caption-coverage-audit-v1','retained_whole_asr_words':whole,'exact_word_selections':exact,'timed_literal_matches_for_overlapping_or_explicit_cues':reconciled,'uncovered_retained_words':missing,'partially_retained_asr_words':partial,'all_retained_asr_words_have_selection_or_timed_literal_match':not missing,'ASR_not_direct_listening':True,'final_graphics_and_silent_readouts_not_audited':True,'partial_source_boundaries_require_separate_review':True,'release_ready':False}

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    for k in ['decisions','transcript','output']:ap.add_argument('--'+k,required=True)
    a=ap.parse_args();dp,tp=Path(a.decisions),Path(a.transcript);db,tb=dp.read_bytes(),tp.read_bytes();d,t=json.loads(db),json.loads(tb);th=hashlib.sha256(tb).hexdigest();assert th==d['transcript_sha256'];r=audit(d,t);r.update(decisions_sha256=hashlib.sha256(db).hexdigest(),transcript_sha256=th)
    with Path(a.output).open('x')as f:json.dump(r,f,ensure_ascii=False,indent=2);f.write('\n')
    print(json.dumps({'uncovered_words':len(r['uncovered_retained_words']),'reconciled_by_timed_text':len(r['timed_literal_matches_for_overlapping_or_explicit_cues']),'release_ready':False}))
