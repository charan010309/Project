/* Behaviour for the user's mobile chat design. Never contains canned AI replies. */
'use strict';
const byId=id=>document.getElementById(id);
const chatInput=byId('chatInput');
let currentThreadId=null, currentThreadTitle='MAXIMUS Chat', isCurrentPinned=false;
let newModalMode='chat', mediaStream=null, pendingAttachment=null, busy=true;
let threads=[], transcript=[], toastTimer, attachmentVersion=0, focusReturn=null;
const MAX_IMAGE_BYTES=2500000;
function statusMessage(text='',error=false){byId('chatStatus').textContent=text;byId('chatStatus').classList.toggle('error',error);}
function showToast(text){const t=byId('toastNotification');byId('toastMessage').textContent=text;clearTimeout(toastTimer);t.classList.remove('opacity-0','translate-y-4');toastTimer=setTimeout(()=>t.classList.add('opacity-0','translate-y-4'),4500);}
async function api(path,method='GET',data){
  const response=await fetch(path,{method,credentials:'same-origin',headers:data?{'Content-Type':'application/json'}:{},body:data?JSON.stringify(data):undefined});
  if(response.status===401){location.assign('/m/login');throw Error('Please sign in again.');}
  const result=await response.json().catch(()=>({}));
  if(!response.ok)throw Error(typeof result.detail==='string'?result.detail:'Request failed ('+response.status+'). Please try again.');
  return result;
}
function setBusy(value){busy=value;byId('sendBtn').disabled=value;chatInput.readOnly=value;byId('activeModelSelect').disabled=value;byId('networkToggle').disabled=value;}
function node(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
function resizeInput(){chatInput.style.height='auto';chatInput.style.height=Math.min(chatInput.scrollHeight,120)+'px';}
chatInput.addEventListener('input',resizeInput);
function applyPrompt(text){if(busy)return;chatInput.value=text;resizeInput();chatInput.focus();}
function toggleSidebar(open){
  const sidebar=byId('appSidebar'),backdrop=byId('sidebarBackdrop');
  sidebar.classList.toggle('-translate-x-full',!open);backdrop.classList.toggle('opacity-0',!open);backdrop.classList.toggle('pointer-events-none',!open);
  if(innerWidth<1024){sidebar.inert=!open;if(open){focusReturn=document.activeElement;sidebar.querySelector('button').focus();}else focusReturn?.focus();}
  document.querySelector('[aria-label="Toggle Navigation Drawer"]')?.setAttribute('aria-expanded',String(open));
}
function showModal(id){focusReturn=document.activeElement;byId(id).classList.remove('hidden');byId(id).querySelector('input,button')?.focus();}
function hideModal(id){byId(id).classList.add('hidden');focusReturn?.focus();}
function openNewConversationModal(){if(busy){showToast('Please wait for the current request.');return;}toggleSidebar(false);byId('newThreadName').value='';showModal('newConversationModal');byId('newThreadName').focus();}
function closeNewConversationModal(){hideModal('newConversationModal');}
function setNewModalMode(mode){newModalMode=mode;for(const m of ['chat','live']){const b=byId(m==='chat'?'modeOptionChat':'modeOptionLive');b.setAttribute('aria-pressed',String(mode===m));b.classList.toggle('border-neutral-900',mode===m);b.classList.toggle('bg-neutral-50',mode===m);}}
function updatePin(){const button=byId('pinConversationBtn');button.setAttribute('aria-pressed',String(isCurrentPinned));button.title=isCurrentPinned?'Unpin Conversation':'Pin Conversation';button.classList.toggle('text-emerald-600',isCurrentPinned);}
function setCurrent(data){currentThreadId=data.id;currentThreadTitle=data.title;transcript=data.messages||[];byId('headerThreadTitle').textContent=data.title;history.replaceState(null,'','/m/home?id='+data.id);isCurrentPinned=!!threads.find(t=>t.id===data.id)?.pinned;updatePin();renderTranscript();}
function appendMessage(role,text,image){
  const user=role==='user';const row=node('article',undefined,'flex items-start gap-2.5 w-full max-w-2xl '+(user?'ml-auto justify-end':'mr-auto'));
  const box=node('div',undefined,'min-w-0 '+(user?'bg-neutral-900 text-white rounded-2xl rounded-tr-sm px-4 py-2.5 max-w-[90%]':'bg-white border border-neutral-200 rounded-2xl rounded-tl-sm p-3.5 flex-1'));
  if(!user)box.append(node('div','MAXIMUS','text-xs font-bold text-emerald-600 mb-2'));
  if(image){const img=node('img');img.src=image;img.alt='Attached image';img.className='attachment';box.append(img);}
  box.append(node('p',text,'text-xs sm:text-sm leading-relaxed'));
  if(!user){const copy=node('button','Copy','mt-2 text-[11px] text-neutral-500');copy.type='button';copy.onclick=async()=>{try{await navigator.clipboard.writeText(text);showToast('Copied response.');}catch{showToast('Clipboard unavailable. Select the text to copy it.');}};box.append(copy);}
  row.append(box);byId('messageList').append(row);return row;
}
function renderTranscript(){byId('messageList').replaceChildren();transcript.forEach(m=>appendMessage(m.role,m.content));byId('welcomeBanner').classList.toggle('hidden',transcript.length>0);scrollToLatest();}
function scrollToLatest(){const area=byId('chatScrollArea');area.scrollTop=area.scrollHeight;}
async function refreshThreads(){
  threads=await api('/api/chat/list');const current=threads.find(t=>t.id===currentThreadId);isCurrentPinned=!!current?.pinned;if(current){currentThreadTitle=current.title;byId('headerThreadTitle').textContent=current.title;}updatePin();
  for(const [id,items] of [['pinnedList',threads.filter(t=>t.pinned)],['recentList',threads.filter(t=>!t.pinned)]]){
    const list=byId(id);list.replaceChildren();
    if(!items.length)list.append(node('li',id==='pinnedList'?'No pinned chats':'No conversations yet','px-2.5 py-1.5 text-neutral-400'));
    for(const thread of items){const li=node('li'),b=node('button',thread.title,'w-full text-left px-2.5 py-1.5 rounded-lg text-neutral-600 hover:bg-neutral-200/50 truncate');b.type='button';b.onclick=()=>selectThread(thread.id);li.append(b);list.append(li);}
  }byId('pinnedBadge').textContent=threads.filter(t=>t.pinned).length;
}
async function selectThread(id){if(busy)return;setBusy(true);statusMessage('Loading conversation…');try{const data=await api('/api/chat/'+encodeURIComponent(id));setCurrent(data);clearAttachment();chatInput.value='';resizeInput();toggleSidebar(false);statusMessage();}catch(e){statusMessage(e.message,true);}finally{setBusy(false);}}
async function createNewThread(){
  if(busy)return;setBusy(true);statusMessage('Creating conversation…');
  try{const title=byId('newThreadName').value.trim()||(newModalMode==='live'?'New Live Session':'New Chat');const data=await api('/api/'+(newModalMode==='live'?'live':'chat')+'/new','POST',{title});
    if(newModalMode==='live'){location.assign('/m/speakwithai?id='+data.id);return;}
    setCurrent(data);clearAttachment();chatInput.value='';resizeInput();closeNewConversationModal();statusMessage();await refreshThreads();
  }catch(e){statusMessage(e.message,true);showToast(e.message);}finally{setBusy(false);}
}
async function togglePinCurrentThread(){if(busy||!currentThreadId)return;if(!transcript.length){showToast('Send a message before pinning this conversation.');return;}setBusy(true);try{await api('/api/chat/'+currentThreadId+'/'+(isCurrentPinned?'unpin':'pin'),'POST');await refreshThreads();showToast(isCurrentPinned?'Conversation pinned':'Conversation unpinned');}catch(e){statusMessage(e.message,true);}finally{setBusy(false);}}
async function shareConversation(){
  if(!transcript.length){showToast('Send a message before sharing.');return;}
  const text=currentThreadTitle+'\n\n'+transcript.map(m=>(m.role==='user'?'You':'MAXIMUS')+': '+m.content).join('\n\n');
  try{if(navigator.share)await navigator.share({title:currentThreadTitle,text});else{await navigator.clipboard.writeText(text);showToast('Conversation text copied.');}}catch(e){if(e.name!=='AbortError')showToast('Sharing unavailable. You can copy individual responses.');}
}
function renderNetwork(){byId('networkToggleLabel').textContent=byId('networkToggle').checked?'On':'Off';}
byId('networkToggle').addEventListener('change',async()=>{const control=byId('networkToggle'),value=control.checked;control.disabled=true;try{await api('/api/settings','PUT',{network_access:value});showToast(value?'Website-opening permission saved. This is not automatic web search.':'Website-opening permission disabled.');}catch(e){control.checked=!value;statusMessage(e.message,true);}finally{renderNetwork();control.disabled=busy;}});
byId('activeModelSelect').addEventListener('change',async()=>{const select=byId('activeModelSelect'),previous=select.dataset.saved;setBusy(true);try{await api('/api/settings','PUT',{model:select.value});select.dataset.saved=select.value;showToast('Default model saved.');}catch(e){select.value=previous;statusMessage(e.message,true);}finally{setBusy(false);}});
function previewAttachment(data){pendingAttachment=data;byId('attachedFileName').textContent=data.name;byId('attachedThumbnail').src=data.dataUrl;byId('attachedThumbnail').classList.remove('hidden');byId('attachedIcon').classList.add('hidden');byId('attachmentStrip').classList.remove('hidden');byId('attachmentStrip').classList.add('flex');}
async function handleFileChange(event){
  if(busy){event.target.value='';return;}const file=event.target.files?.[0];if(!file)return;const version=++attachmentVersion;
  try{if(!['image/jpeg','image/png','image/webp'].includes(file.type))throw Error('Choose a JPEG, PNG, or WebP image.');if(file.size>MAX_IMAGE_BYTES)throw Error('Choose an image smaller than 2.5 MB.');
    const dataUrl=await new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(r.result);r.onerror=()=>reject(Error('Unable to read this image.'));r.readAsDataURL(file);});if(version===attachmentVersion)previewAttachment({dataUrl,name:file.name,mime:file.type});
  }catch(e){event.target.value='';statusMessage(e.message,true);}
}
function clearAttachment(){attachmentVersion++;pendingAttachment=null;byId('fileUploadInput').value='';byId('attachedThumbnail').removeAttribute('src');byId('attachmentStrip').classList.add('hidden');byId('attachmentStrip').classList.remove('flex');}
async function openCameraModal(){
  if(busy)return;showModal('cameraModal');
  try{if(!navigator.mediaDevices?.getUserMedia)throw Error('Camera is unavailable. Use image upload instead.');const stream=await navigator.mediaDevices.getUserMedia({video:{facingMode:'environment'},audio:false});if(byId('cameraModal').classList.contains('hidden')){stream.getTracks().forEach(t=>t.stop());return;}mediaStream=stream;byId('cameraStream').srcObject=stream;await byId('cameraStream').play();}catch(e){closeCameraModal();statusMessage('Camera unavailable or permission denied. You can attach an image instead.',true);}
}
function closeCameraModal(){mediaStream?.getTracks().forEach(t=>t.stop());mediaStream=null;byId('cameraStream').srcObject=null;hideModal('cameraModal');}
function snapCameraPhoto(){
  const video=byId('cameraStream'),canvas=byId('cameraCanvas');if(!video.videoWidth){showToast('Wait for the camera to become ready.');return;}
  const scale=Math.min(1,1600/Math.max(video.videoWidth,video.videoHeight));canvas.width=Math.round(video.videoWidth*scale);canvas.height=Math.round(video.videoHeight*scale);canvas.getContext('2d').drawImage(video,0,0,canvas.width,canvas.height);const dataUrl=canvas.toDataURL('image/jpeg',.8);if(dataUrl.length>MAX_IMAGE_BYTES*4/3){showToast('Photo is too large. Try an uploaded image.');return;}attachmentVersion++;previewAttachment({dataUrl,name:'camera-photo.jpg',mime:'image/jpeg'});closeCameraModal();
}
window.addEventListener('pagehide',()=>mediaStream?.getTracks().forEach(t=>t.stop()));
function handleInputKeydown(event){if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing&&matchMedia('(pointer:fine)').matches){event.preventDefault();handleMessageSubmit(event);}}
async function handleMessageSubmit(event){
  event?.preventDefault();if(busy)return;const text=chatInput.value.trim(),attachment=pendingAttachment;if(!text&&!attachment)return;
  const model=byId('activeModelSelect').value;if(attachment&&model!=='flash'){statusMessage('Image analysis requires MAXIMUS Flash. Switch models to send this image.',true);return;}
  setBusy(true);statusMessage('MAXIMUS is thinking…');
  try{
    if(!currentThreadId){const fresh=await api('/api/chat/new','POST',{title:'New Chat'});setCurrent(fresh);}else await api('/api/chat/'+currentThreadId);
    const data=await api(attachment?'/ask-image':'/ask','POST',attachment?{text:text||'Describe this image.',image:attachment.dataUrl.split(',')[1],mime_type:attachment.mime,model}:{text,model});
    // Render only the persisted result; keep the draft and attachment on failures.
    currentThreadId=data.chat_id;currentThreadTitle=data.title||currentThreadTitle;byId('headerThreadTitle').textContent=currentThreadTitle;history.replaceState(null,'','/m/home?id='+currentThreadId);
    const content=text||'Describe this image.';transcript.push({role:'user',content},{role:'assistant',content:data.reply});byId('welcomeBanner').classList.add('hidden');appendMessage('user',content,attachment?.dataUrl);appendMessage('assistant',data.reply);chatInput.value='';resizeInput();clearAttachment();statusMessage();scrollToLatest();
    try{await refreshThreads();}catch{showToast('Reply saved. History could not refresh; reopen the menu later.');}
  }catch(e){statusMessage(e.message+' Your draft has been kept.',true);scrollToLatest();}finally{setBusy(false);}
}
document.addEventListener('keydown',event=>{
  const modal=['cameraModal','newConversationModal'].map(byId).find(x=>!x.classList.contains('hidden'));
  const drawer=innerWidth<1024&&!byId('appSidebar').classList.contains('-translate-x-full')?byId('appSidebar'):null;
  if(event.key==='Escape'){if(modal?.id==='cameraModal')closeCameraModal();else if(modal)closeNewConversationModal();else if(drawer)toggleSidebar(false);}
  const area=modal||drawer;if(event.key==='Tab'&&area){const items=[...area.querySelectorAll('button:not(:disabled),input,a[href],select')].filter(n=>n.getClientRects().length);const first=items[0],last=items.at(-1);if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}}
});
const wide=matchMedia('(min-width:1024px)');function syncDrawer(){byId('appSidebar').inert=!wide.matches&&byId('appSidebar').classList.contains('-translate-x-full');}wide.addEventListener('change',syncDrawer);syncDrawer();
async function initialize(){
  statusMessage('Loading your workspace…');
  try{
    const [profile,settings,providers]=await Promise.all([api('/api/user/profile'),api('/api/settings'),api('/api/providers')]);
    byId('profileName').textContent=profile.name;byId('profileEmail').textContent=profile.email;
    const select=byId('activeModelSelect');for(const p of providers.filter(p=>p.is_valid&&p.is_active))for(const model of p.models){const option=node('option',p.label+' · '+model);option.value='provider:'+p.id+':'+model;select.append(option);}
    if(![...select.options].some(o=>o.value===settings.model)){const option=node('option','Saved model (unavailable)');option.value=settings.model;select.append(option);}
    select.value=settings.model;select.dataset.saved=settings.model;byId('networkToggle').checked=settings.network_access;renderNetwork();
    const id=new URLSearchParams(location.search).get('id');if(id){if(!/^\d+$/.test(id))throw Error('Invalid conversation link.');setCurrent(await api('/api/chat/'+id));}
    await refreshThreads();statusMessage();setBusy(false);
  }catch(e){statusMessage(e.message,true);const retry=node('button','Retry loading','ml-2 underline');retry.onclick=()=>location.reload();byId('chatStatus').append(retry);}
}
initialize();
