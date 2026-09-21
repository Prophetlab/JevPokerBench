import {useEffect,useSyncExternalStore} from 'react';
import {Volume2,VolumeX} from 'lucide-react';
import {t} from './i18n';
import {audioSnapshot,setAudioMuted,setAudioVolume,subscribeAudio,unlockAudio} from './tableAudio';

export function SoundControls(){
    const {volume,muted,unlocked}=useSyncExternalStore(subscribeAudio,audioSnapshot);
    useEffect(()=>{
        const unlock=()=>{void unlockAudio();};
        window.addEventListener('pointerdown',unlock,{once:true});window.addEventListener('keydown',unlock,{once:true});
        return()=>{window.removeEventListener('pointerdown',unlock);window.removeEventListener('keydown',unlock);};
    },[]);
    return <div className="sound-controls" role="group" aria-label={t('牌桌音效')}>
      <button type="button" className="sound-toggle" aria-label={t(muted?'开启音效':'静音')} aria-pressed={muted} onClick={()=>{void unlockAudio();setAudioMuted(!muted);}} title={t(muted?'开启音效':'静音')}>{muted||volume===0?<VolumeX size={16}/>:<Volume2 size={16}/>}</button>
      <label><span>{t('音量')}</span><input type="range" min="0" max="100" step="1" aria-label={t('音量')} value={Math.round(volume*100)} onChange={e=>{void unlockAudio();setAudioVolume(+e.target.value/100);}}/></label>
      <span className="sound-hint">{t(unlocked?'原创合成音效':'操作后开启音效')}</span>
    </div>;
}
