// Run generated JSX against a minimal JavaScript mock. This is NOT an AE import.
// node engine/validate_nle_jsx.js exports/A-pilot-40s-nle/after-effects.jsx [result.json]
const fs=require('fs'),vm=require('vm'),path=require('path');
const scriptPath=path.resolve(process.argv[2]);const layers=[];let comp;
class Prop{
  constructor(name){this.name=name;this.numKeys=0;this.value={resetCharStyle(){}};this.keys=[];this.props={};}
  property(n){return this.props[n]||(this.props[n]=new Prop(n));}
  removeKey(i){this.keys.splice(i-1,1);this.numKeys=this.keys.length;}
  setValue(v){if(Array.isArray(v)&&v.some(x=>!Number.isFinite(x)))throw Error('NaN '+this.name);this.value=v;}
  setValuesAtTimes(ts,vs){if(ts.length!==vs.length||!ts.length||vs.some(v=>!Number.isFinite(v))||ts.some(v=>!Number.isFinite(v)))throw Error('Invalid remap keys');this.keys=ts.map((t,i)=>[t,vs[i]]);this.numKeys=ts.length;}
  setInterpolationTypeAtKey(i,a,b){if(a!=='HOLD'||b!=='HOLD')throw Error('Interpolation is not HOLD');}
}
class Layer{
  constructor(src,text){this.src=src;this.text=text;this.p={};layers.push(this);}
  property(n){return this.p[n]||(this.p[n]=new Prop(n));}
  sourceRectAtTime(){return {top:10,left:0,width:500,height:50};}
}
function File(p){if(!(this instanceof File))return new File(p);this.fsName=path.resolve(p);this.exists=fs.existsSync(this.fsName);this.parent={fsName:path.dirname(this.fsName)};}
const side=JSON.parse(fs.readFileSync(path.join(path.dirname(scriptPath),'nle-manifest.json'),'utf8'));
const project=JSON.parse(fs.readFileSync(side.project_path,'utf8'));
const context={File,ImportOptions:function(f){this.file=f},Number,KeyframeInterpolationType:{HOLD:'HOLD'},ParagraphJustification:{LEFT_JUSTIFY:1},AlphaMode:{STRAIGHT:1},$:{fileName:scriptPath},alert:()=>{},app:{beginUndoGroup(){},endUndoGroup(){},fonts:{getFontsByPostScriptName:()=>[]},project:{importFile(o){const meta=Object.values(side.source_timeline.source_metadata).find(m=>m.path===o.file.fsName);return {path:o.file.fsName,width:meta?.width||1920,height:meta?.height||1080,mainSource:{}};},items:{addFolder:()=>({}),addComp:(name,w,h,ratio,duration,fps)=>comp={name,width:w,height:h,duration,frameRate:fps,frameDuration:1/fps,layers:{add:src=>new Layer(src),addText:s=>new Layer(null,s)},openInViewer(){}}}}}};
vm.runInNewContext(fs.readFileSync(scriptPath,'utf8').replace('#target aftereffects',''),context,{timeout:10000});
const video=layers.filter(l=>l.name.includes('[HOLD native'));
const editable=layers.filter(l=>l.name.includes('[EDIT TEXT'));
const audio=layers.filter(l=>l.name.includes('[raw audio;'));
const keys=video.flatMap(l=>{
  const c=project.clips.find(c=>l.name===c.id+' [HOLD native frame map]');
  const r=side.source_timeline.source_metadata[c.source].fps.split('/').map(Number),fps=r[0]/(r[1]||1);
  if(l.audioEnabled!==false)throw Error('Video accidentally has raw audio enabled');
  return l.property('ADBE Time Remapping').keys.map(([t,v])=>({frame:Math.round(t*comp.frameRate),source:c.source,source_frame:Math.round(v*fps)}));
}).sort((a,b)=>a.frame-b.frame);
if(JSON.stringify(keys)!==JSON.stringify(side.source_timeline.frames.map(f=>({frame:f.frame,source:f.source,source_frame:f.source_frame}))))throw Error('AE remap differs from declared native frame map');
const expectedEditable=side.components.filter(c=>c.editable_text).length;
if(editable.length!==expectedEditable||audio.length!==side.audio_events.length||video.length!==project.clips.length||layers.length!==video.length+editable.length+audio.length+side.components.length)throw Error('Layer count mismatch');
for(let i=0;i<audio.length;i++){
  const a=side.audio_events[i],l=audio[i];
  if(Math.abs(l.startTime-(a.in-a.source_in))>1e-9||l.inPoint!==a.in||l.outPoint!==a.out||l.enabled!==false||l.audioEnabled!==true)throw Error('Audio time/switch loss');
}
if(editable.some(l=>l.enabled!==false))throw Error('Native candidate text enabled unexpectedly');
const result={pass:true,kind:'JavaScript mock execution, NOT actual Adobe import',video_layers:video.length,editable_text_alternatives:editable.length,png_components:side.components.length,raw_audio_layers:audio.length,total_layers:layers.length,remap_keys_compared:keys.length,source_remap_values_exact:true,audio_subframe_times_preserved:true};
if(process.argv[3]){const dest=process.argv[3];fs.writeFileSync(dest+'.tmp',JSON.stringify(result,null,2)+'\n');fs.renameSync(dest+'.tmp',dest);}
console.log(JSON.stringify(result,null,2));
