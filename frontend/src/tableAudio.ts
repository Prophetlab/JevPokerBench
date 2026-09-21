// Original synthesized cues: no recordings, samples, or third-party assets.
export type Cue='action'|'board'|'win';
type AudioSettings={volume:number;muted:boolean;unlocked:boolean};
let settings:AudioSettings={volume:0.12,muted:false,unlocked:false};
let context:AudioContext|undefined;
let master:GainNode|undefined;
const listeners=new Set<()=>void>();
export const audioSnapshot=()=>settings;
export const subscribeAudio=(listener:()=>void)=>{listeners.add(listener);return()=>{listeners.delete(listener);};};
function update(next:Partial<AudioSettings>){settings={...settings,...next};if(master&&context)master.gain.setValueAtTime(settings.muted?0:settings.volume,context.currentTime);listeners.forEach(listener=>listener());}
export function setAudioVolume(volume:number){update({volume:Math.max(0,Math.min(1,volume))});}
export function setAudioMuted(muted:boolean){update({muted});}
export async function unlockAudio(){
    try{
        if(!context){context=new AudioContext();master=context.createGain();master.gain.value=settings.muted?0:settings.volume;master.connect(context.destination);}
        if(context.state==='suspended')await context.resume();
        update({unlocked:context.state==='running'});
    }catch{/* Sound is optional when WebAudio is unavailable or blocked. */}
}
export function playCue(cue:Cue){
    if(!context||!master||context.state!=='running'||!settings.unlocked||settings.muted||!settings.volume)return;
    const notes=cue==='win'?[523.25,659.25,783.99]:cue==='board'?[392,523.25]:[280];
    notes.forEach((frequency,index)=>{
        const start=context!.currentTime+index*0.085,osc=context!.createOscillator(),envelope=context!.createGain();
        osc.type='sine';osc.frequency.value=frequency;
        envelope.gain.setValueAtTime(0,start);envelope.gain.linearRampToValueAtTime(0.18,start+0.012);envelope.gain.exponentialRampToValueAtTime(0.001,start+0.13);
        osc.connect(envelope);envelope.connect(master!);osc.start(start);osc.stop(start+0.15);
        osc.onended=()=>{osc.disconnect();envelope.disconnect();};
    });
}
// High-water marks survive component remounts, polling and replay seeks. Initial
// snapshots are silent; going backwards cannot retrigger an already seen event.
export class SoundProgress {
    private seen=new Map<string,number>();
    advance(scope:string,hand:number,index:number){
        const key=`${scope}:${hand}`,previous=this.seen.get(key);
        this.seen.set(key,Math.max(previous??index,index));
        return previous!==undefined&&index>previous;
    }
}
export const soundProgress=new SoundProgress();
