// Focused generated-JSX execution. Minimal mock, NOT Adobe rendering/import.
// node engine/selftest_character_reveal_nle.js exports/.../after-effects.jsx result.json
const fs=require('fs'),vm=require('vm'),path=require('path');
const filename=path.resolve(process.argv[2]),base=path.dirname(filename);
const side=JSON.parse(fs.readFileSync(path.join(base,'nle-manifest.json'),'utf8'));
const layers=[];let comp;
class Prop {
 constructor(){this.numKeys=0;this.props={};this.value={resetCharStyle(){}};}
 property(n){return this.props[n]||(this.props[n]=new Prop());}
 setValue(v){this.value=v;} removeKey(){this.numKeys--;}
 setValuesAtTimes(t,v){this.numKeys=t.length;this.keys=t.map((x,i)=>[x,v[i]]);}
 setInterpolationTypeAtKey(i,a,b){if(a!=='HOLD'||b!=='HOLD')throw Error('Not HOLD');}
}
class Layer {
 constructor(src,text){this.src=src;this.text=text;this.p={};layers.push(this);}
 property(n){return this.p[n]||(this.p[n]=new Prop());}
 sourceRectAtTime(){return {left:0,top:10,width:500,height:50};}
}
function File(p){if(!(this instanceof File))return new File(p);this.fsName=path.resolve(p);this.exists=fs.existsSync(this.fsName);this.parent={fsName:path.dirname(this.fsName)};}
const context={File,ImportOptions:function(f){this.file=f;},KeyframeInterpolationType:{HOLD:'HOLD'},ParagraphJustification:{LEFT_JUSTIFY:1},AlphaMode:{STRAIGHT:1},$:{fileName:filename},alert(){},app:{beginUndoGroup(){},endUndoGroup(){},fonts:{getFontsByPostScriptName:()=>[]},project:{importFile(o){return {path:o.file.fsName,width:2560,mainSource:{}};},items:{addFolder:()=>({}),addComp:(name,w,h,r,d,fps)=>comp={frameRate:fps,frameDuration:1/fps,layers:{add:s=>new Layer(s),addText:t=>new Layer(null,t)},openInViewer(){}}}}}};
vm.runInNewContext(fs.readFileSync(filename,'utf8').replace('#target aftereffects',''),context,{timeout:10000});
const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
let states=0;
for(const r of side.components){
 const found=layers.filter(l=>l.name===r.id);if(found.length!==1)throw Error('Missing/duplicate component '+r.id);
 const l=found[0];
 if(l.src.path!==path.resolve(base,r.path)||l.inPoint!==r.in||l.outPoint!==r.out||!same(l.property('ADBE Transform Group').property('ADBE Position').value,r.position)||!same(l.property('ADBE Transform Group').property('ADBE Anchor Point').value,[0,0]))throw Error('PNG asset/position/lifetime differs '+r.id);
 if(r.character_reveal_state){
  states++;
  if(r.editable_text!==null||layers.some(l=>l.name===r.event_id+' [EDIT TEXT - candidate font]'))throw Error('Reveal gained misleading editable text');
  if(!l.comment.includes('No native editable text alternative'))throw Error('Missing raster-state disclosure');
 }
}
if(states===0)throw Error('This dedicated fixture requires reveal states');
const result={pass:true,kind:'Generated JSX execution against mock; NOT actual AE import or rendering',components_compared:side.components.length,reveal_states_compared:states,asset_position_in_out_exact:true,reveal_native_editable_text_alternatives:0};
if(process.argv[3]){fs.writeFileSync(process.argv[3]+'.tmp',JSON.stringify(result,null,2)+'\n');fs.renameSync(process.argv[3]+'.tmp',process.argv[3]);}
console.log(JSON.stringify(result));
