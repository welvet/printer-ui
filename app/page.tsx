'use client';
import {useEffect,useRef,useState} from 'react';
type Data = Record<string, any>;
const states=['Starting','Ready','Printing','Filament operation','Filament operation','Bed leveling','PID calibration','Resonance test','Self check','Updating','Homing','File transfer','Timelapse','Extruder operation','Emergency stop','Power recovery'];
const time=(s:number|undefined)=>s==null?'—':`${Math.floor(s/3600)}h ${Math.floor(s%3600/60)}m`;
function CameraFeed(){
 const image=useRef<HTMLImageElement>(null);
 const retry=useRef<ReturnType<typeof setTimeout>|null>(null);
 const [source,setSource]=useState<string|null>(null);
 const [unavailable,setUnavailable]=useState(false);
 const reconnect=()=>{setUnavailable(false);setSource(`/camera/?v=${Date.now()}`);};
 useEffect(()=>{
  // Start after hydration: an MJPEG request in static HTML can fail before
  // React attaches error handlers, leaving a blank feed until manual refresh.
  reconnect();
  const resume=()=>{if(document.visibilityState==='visible')reconnect();};
  document.addEventListener('visibilitychange',resume);
  window.addEventListener('online',reconnect);
  return ()=>{document.removeEventListener('visibilitychange',resume);window.removeEventListener('online',reconnect);};
 },[]);
 useEffect(()=>{
  let disposed=false,checking=false,wasOffline=false,lastVersion:number|null=null;
  let lastAdvance=Date.now(),lastReconnect=0;
  const check=async()=>{
   if(checking)return;
   checking=true;
   try{
    const response=await fetch('/api/health',{cache:'no-store',signal:AbortSignal.timeout(4000)});
    if(!response.ok)throw new Error('Backend unavailable');
    const health=await response.json();
    if(disposed)return;
    if(!health.camera_connected){wasOffline=true;setUnavailable(true);return;}
    const now=Date.now();
    if(lastVersion!==health.camera_frames){lastAdvance=now;lastVersion=health.camera_frames;}
    const stalled=now-lastAdvance>15000;
    setUnavailable(stalled);
    // Dimensions alone cannot detect a frozen MJPEG image after a reboot.
    if((wasOffline||!image.current?.naturalWidth||stalled)&&now-lastReconnect>10000){
     lastReconnect=now;reconnect();setUnavailable(stalled);
    }
    wasOffline=false;
   }catch{if(!disposed){wasOffline=true;setUnavailable(true);}}
   finally{checking=false;}
  };
  const watchdog=setInterval(check,3000);
  return ()=>{disposed=true;clearInterval(watchdog);if(retry.current)clearTimeout(retry.current);};
 },[]);
 const failed=()=>{setUnavailable(true);if(retry.current)clearTimeout(retry.current);retry.current=setTimeout(reconnect,2000);};
 return <section className="camera panel"><div className="panelhead"><h2>Camera</h2><button className="quiet" onClick={reconnect} aria-label="Reconnect camera" title="Reconnect camera"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="M20 7v5h-5M4 17v-5h5"/><path d="M6 7a7 7 0 0 1 12-1l2 6M4 12l2 6a7 7 0 0 0 12-1"/></svg></button></div><div className="feed">{source&&<img ref={image} key={source} src={source} alt="Live view inside the Centauri Carbon 2" onLoad={()=>setUnavailable(false)} onError={failed}/ >}{unavailable&&<p role="status">Reconnecting camera…</p>}</div></section>;
}
export default function Home(){
 const [data,setData]=useState<Data>({}),[attrs,setAttrs]=useState<Data>({}),[connection,setConnection]=useState('Connecting…'),[last,setLast]=useState(0),[now,setNow]=useState(0),[confirm,setConfirm]=useState(false),[notice,setNotice]=useState(''),[pending,setPending]=useState(false);
 useEffect(()=>{
  const events=new EventSource('/api/events');
  events.onmessage=e=>{try{const state=JSON.parse(e.data);setData(state.data);setAttrs(state.attributes);setLast(state.updated_at*1000);setConnection(state.connected?'Connected':'Printer unavailable');if(!state.connected)setConfirm(false);}catch{setConnection('Invalid status');}};
  events.onerror=()=>{setConnection('Reconnecting…');setConfirm(false);};
  const clock=setInterval(()=>setNow(Date.now()),1000);
  return ()=>{events.close();clearInterval(clock);};
 },[]);
 const fresh=connection==='Connected'&&last>0&&now-last<25000;
 const machine=data.machine_status||{},job=data.print_status||{},progress=Math.max(0,Math.min(100,job.progress??machine.progress??0));
 const active=fresh&&machine.status===2&&!['complete','cancelled','error'].includes(job.state)&&![2077,2503,2504].includes(machine.sub_status);
 const stop=async()=>{
  if(!active||pending){setConfirm(false);setNotice('Printer unavailable.');return;}
  setConfirm(false);setPending(true);setNotice('Stopping…');
  try{const response=await fetch('/api/stop',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({confirmed:true,job_id:job.uuid}),signal:AbortSignal.timeout(15000)});const result=await response.json();setNotice(response.ok?'Stop acknowledged.':result.detail||'Could not stop print.');}
  catch{setNotice('No acknowledgement. Check the printer.');}
  finally{setPending(false);}
 };
 return <main><header><a className="brand" href="/"><span className="brandmark" aria-hidden="true"/><span>Print view</span></a><span className={'connection '+(fresh?'online':'')}><i/>{connection}</span></header>
 <section className="heading"><div><h1>{attrs.hostname||'Centauri Carbon 2'}</h1><p className="muted">ELEGOO</p></div><div className="state">{fresh?(job.state==='paused'?'Paused':states[machine.status]||'Unknown'):'Awaiting live status'}</div></section>
 <div className="grid"><CameraFeed/>
 <aside><section className="panel job"><p className="eyebrow">Current print</p><h2 className="filename">{job.filename||'No active print'}</h2><div className="progress"><strong>{last?progress:'—'}<small>%</small></strong><span>{job.current_layer!=null?`Layer ${job.current_layer}${job.total_layer?` / ${job.total_layer}`:''}`:'Waiting for printer'}</span></div><div className="track"><div style={{width:`${progress}%`}}/></div><div className="times"><div><span>Elapsed</span><b>{time(job.print_duration)}</b></div><div><span>Remaining</span><b>{time(job.remaining_time_sec)}</b></div></div><div className="controls"><button className="stop" disabled={!active||pending} onClick={()=>setConfirm(true)}>{pending?'Stopping…':'Stop print'}</button></div>{notice&&<p role="status" className="notice">{notice}</p>}</section></aside></div>
 <section className="temps">{[['Nozzle',data.extruder],['Print bed',data.heater_bed],['Chamber',data.ztemperature_sensor]].map(([label,value])=>{const v=value as Data|undefined;return <div className="panel temperature" key={String(label)}><p className="eyebrow">{String(label)}</p><strong>{v?.temperature!=null?Math.round(v.temperature):'—'}<small>°C</small></strong>{v?.target!=null&&<span>Target {v.target}°C</span>}</div>})}</section>
 <footer><span>{attrs.ip||'—'}</span><span>{last?`Updated ${Math.max(0,Math.floor((now-last)/1000))}s ago`:'Connecting to printer'}</span></footer>
 {confirm&&<div className="overlay" onClick={()=>setConfirm(false)}><section className="dialog" role="alertdialog" aria-modal="true" aria-labelledby="stop-title" onKeyDown={e=>{if(e.key==='Escape')setConfirm(false)}} onClick={e=>e.stopPropagation()}><h2 id="stop-title">Stop this print?</h2><p>This cancels <b>{job.filename}</b>. You will need to start the job again.</p><div className="dialogactions"><button autoFocus onClick={()=>setConfirm(false)}>Keep printing</button><button className="stop" disabled={!active} onClick={stop}>Yes, stop print</button></div></section></div>}
 </main>
}
