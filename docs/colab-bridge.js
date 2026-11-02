// Export a self-contained job for the user's authenticated Colab GPU session.
const jobButton=document.createElement('button');jobButton.id='exportSAM3Job';jobButton.className='wide';jobButton.textContent='Export SAM 3 job for Colab';
const colabHelp=document.createElement('button');colabHelp.className='wide secondary';colabHelp.textContent='SAM 3 setup & notebook →';
const bridgeInfo=document.createElement('small');bridgeInfo.textContent='GPU workflow: export job → upload to Colab notebook → import its result in the Results tab. Exporting does not run SAM 3 or upload imagery.';
el('compare').insertAdjacentElement('afterend',jobButton);jobButton.insertAdjacentElement('afterend',colabHelp);colabHelp.insertAdjacentElement('afterend',bridgeInfo);
colabHelp.onclick=()=>{if(parent!==window)parent.postMessage({type:'geodevelop-open-colab'},'*');else location.href='dashboard.html#colab';};
jobButton.onclick=()=>{
 const a=state.before,b=state.after;
 if(!a||!b)return say('Upload both images first.');
 if(!el('aligned').checked)return say('Check image alignment and tick the confirmation first.');
 if(a.width!==b.width||a.height!==b.height)return say('The SAM 3 pilot requires identical dimensions and aligned extent.');
 if(Math.max(a.width,a.height)>4096||a.width*a.height>8000000)return say('Use a pilot crop under 8 megapixels and at most 4096 pixels per side.');
 const beforeDate=el('beforeDate').value,afterDate=el('afterDate').value;
 if(beforeDate&&afterDate&&beforeDate>afterDate)return say('The before date must not be later than the after date.');
 const job={schema:'geodevelop.sam3.job.v1',jobId:crypto.randomUUID(),created:new Date().toISOString(),title:el('title').value,alignmentConfirmed:true,georeferenced:false,illustrative:a.demo||b.demo,before:{name:a.name,date:beforeDate||null,image:a.canvas.toDataURL('image/png')},after:{name:b.name,date:afterDate||null,image:b.canvas.toDataURL('image/png')},notes:'Aligned opaque RGB pilot images; image coordinates only. Prompt building. No automated registration.'};
 if(parent!==window)parent.postMessage({type:'geodevelop-export-job',job},'*');else download(new Blob([JSON.stringify(job)],{type:'application/json'}),'geodevelop_job.json');say('SAM 3 job exported. Next: open the Colab notebook and upload this JSON. Model inference has not run yet.');
};
if(new URLSearchParams(location.search).has('embedded')){const style=document.createElement('style');style.textContent='header,footer,aside>a{display:none}';document.head.append(style);}
