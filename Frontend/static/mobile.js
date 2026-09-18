/* Dedicated mobile UI. All account data comes from the authenticated API. */
(() => {
  'use strict';
  const $ = selector => document.querySelector(selector);
  const page = $('#page'), status = $('#status'), kind = document.body.dataset.mobilePage;
  const languages = {'en-IN':'English (India)','hi-IN':'Hindi','te-IN':'Telugu','ta-IN':'Tamil','kn-IN':'Kannada','ml-IN':'Malayalam','fr-FR':'French','ru-RU':'Russian','ja-JP':'Japanese'};
  const el = (tag, text, cls) => { const node=document.createElement(tag); if(text!==undefined) node.textContent=text; if(cls) node.className=cls; return node; };
  function notice(text='', error=false) { status.textContent=text; status.classList.toggle('error',error); }
  async function api(path, method='GET', body, blob=false) {
    const response=await fetch(path,{method,credentials:'same-origin',headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined});
    if(response.status===401) { location.assign('/m/login'); throw new Error('Please sign in again.'); }
    if(!response.ok) { const data=await response.json().catch(()=>({})); throw new Error(typeof data.detail==='string'?data.detail:'Request failed ('+response.status+'). Please try again.'); }
    return blob?response.blob():response.json();
  }
  function action(text, task, cls='') {
    const button=el('button',text,cls); button.type='button';
    button.addEventListener('click',async()=>{button.disabled=true;try{await task(button);}catch(error){notice(error.message,true);}finally{button.disabled=false;}}); return button;
  }
  function field(label, name, type='text', value='') {
    const wrapper=el('label',undefined,'field');wrapper.append(el('span',label));
    const input=el(type==='textarea'?'textarea':'input');input.name=name;if(type!=='textarea') input.type=type;input.value=value??'';wrapper.append(input);return wrapper;
  }
  function select(label,name,options,value) {
    const wrapper=el('label',undefined,'field');wrapper.append(el('span',label));const input=el('select');input.name=name;
    for(const [key,text] of Object.entries(options)){const option=el('option',text);option.value=key;input.append(option);}input.value=value;wrapper.append(input);return wrapper;
  }
  function link(text,url,cls='button') {const a=el('a',text,cls);a.href=url;return a;}
  function formSubmit(form,label,save) {
    const button=el('button',label,'primary');button.type='submit';form.append(button);
    form.addEventListener('submit',async event=>{event.preventDefault();if(button.disabled)return;button.disabled=true;notice('Saving…');try{await save(Object.fromEntries(new FormData(form)));notice('Saved.');}catch(error){notice(error.message,true);}finally{button.disabled=false;}});
  }
  async function modelOptions(current) {
    const options={flash:'MAXIMUS Flash'};
    const providers=await api('/api/providers');
    for(const provider of providers.filter(p=>p.is_active&&p.is_valid)) for(const model of provider.models) options['provider:'+provider.id+':'+model]=provider.label+' · '+model;
    if(current&&!options[current]) options[current]='Saved model (currently unavailable)';return options;
  }
  function message(container, role, text, voice=false, language) {
    const item=el('article',undefined,'message '+(role==='user'?'user':'assistant'));
    item.append(el('span',role==='user'?'You':'MAXIMUS','role'),el('p',text));
    if(role!=='user') {
      item.append(action('Copy',async b=>{await navigator.clipboard.writeText(text);b.textContent='Copied';}));
      if(voice) item.append(action('Listen',async button=>{
        button.textContent='Preparing audio…';
        try {const data=await api('/api/tts','POST',{text,output_language:language},true);const url=URL.createObjectURL(data);const audio=el('audio');audio.controls=true;audio.src=url;item.querySelector('audio')?.remove();item.append(audio);audio.addEventListener('ended',()=>URL.revokeObjectURL(url),{once:true});await audio.play().catch(()=>notice('Tap play to listen.'));}
        finally{button.textContent='Listen';}
      }));
    }
    container.append(item);return item;
  }
  async function chat(voice=false) {
    const settings=await api('/api/settings');
    const controls=el('div',undefined,'row between');
    const model=select('Model','model',await modelOptions(settings.model),settings.model);model.classList.add('grow');
    if(!voice) controls.append(model);else controls.append(el('p','Speak a message or type below.','muted'));
    let currentId=null, busy=false;
    const base=voice?'/api/live/':'/api/chat/';
    const newButton=action('New',async()=>{if(busy)return;const data=await api(base+'new','POST',{title:voice?'New Live Session':'New Chat'});currentId=data.id;history.replaceState(null,'','?id='+currentId);messages.replaceChildren();empty.hidden=false;notice('New conversation ready.');});newButton.disabled=true;controls.append(newButton);page.append(controls);
    const empty=el('section',undefined,'empty');empty.append(el('span','M','brand-mark'),el('h2',voice?'A little help, out loud.':'What can we work on?'),el('p',voice?'Tap the microphone to dictate. You can review your words before sending.':'Ask a question, explore an idea, or get something done.','muted'));
    const suggestions=el('div',undefined,'suggestions');for(const prompt of ['Help me plan my day','Explain a topic simply','Help me write a message']) suggestions.append(action(prompt,()=>{input.value=prompt;input.focus();}));if(!voice)empty.append(suggestions);page.append(empty);
    const messages=el('div',undefined,'messages');messages.setAttribute('role','log');messages.setAttribute('aria-label','Conversation');page.append(messages);
    const form=el('form',undefined,'composer');const input=el('textarea');input.id='chat-textarea';input.name='message';input.placeholder=voice?'Speak or type a message…':'Message MAXIMUS…';input.setAttribute('aria-label','Message');input.required=true;input.maxLength=20000;form.append(input);
    const row=el('div',undefined,'row between');const send=el('button','Send','primary');send.type='submit';send.disabled=true;
    if(voice){
      const Recognition=window.SpeechRecognition||window.webkitSpeechRecognition;
      if(Recognition){const recognition=new Recognition();recognition.lang=settings.input_language||'en-IN';recognition.interimResults=false;let recording=false;
        const mic=action('Microphone',()=>{if(recording){recognition.stop();return;}recognition.start();});
        recognition.onstart=()=>{recording=true;mic.textContent='Stop listening';notice('Listening…');};
        recognition.onresult=e=>{input.value+=(input.value?' ':'')+e.results[0][0].transcript;notice('Review your message, then send.');};
        recognition.onerror=e=>notice(e.error==='not-allowed'?'Microphone permission was denied. You can still type.':'Speech input failed. Please try again or type your message.',true);
        recognition.onend=()=>{recording=false;mic.textContent='Microphone';};window.addEventListener('pagehide',()=>recognition.abort());row.append(mic);
      }else row.append(el('span','Voice input is unavailable in this browser. Use typing below.','hint'));
    }else row.append(el('span','AI can make mistakes.','hint'));
    row.append(send);form.append(row);page.append(form);
    const id=new URLSearchParams(location.search).get('id');
    if(id){if(!/^\d+$/.test(id))throw new Error('Invalid conversation link.');const data=await api(base+id);currentId=data.id;for(const msg of data.messages)message(messages,msg.role,msg.content,voice,settings.output_language);empty.hidden=data.messages.length>0;}
    else {const data=await api(base+'new','POST',{title:voice?'New Live Session':'New Chat'});currentId=data.id;history.replaceState(null,'','?id='+currentId);}
    form.addEventListener('submit',async event=>{
      event.preventDefault();const text=input.value.trim();if(!text||busy)return;busy=true;send.disabled=true;newButton.disabled=true;input.readOnly=true;notice('MAXIMUS is thinking…');
      try{
        // Re-select this conversation: another open tab may have changed the cookie session.
        await api(base+currentId);
        const data=await api(voice?'/api/live-ask':'/ask','POST',voice?{text,output_language:settings.output_language}:{text,model:model.querySelector('select').value});
        empty.hidden=true;message(messages,'user',text);message(messages,'assistant',data.reply,voice,data.output_language);input.value='';notice('');messages.lastElementChild.scrollIntoView({block:'nearest'});
      }catch(error){notice(error.message+' Your message is still here to retry.',true);}finally{busy=false;send.disabled=false;newButton.disabled=false;input.readOnly=false;}
    });
    send.disabled=false;newButton.disabled=false;notice('');
  }
  async function historyPage() {
    const [chats,lives]=await Promise.all([api('/api/chat/list'),api('/api/live/list')]);
    const entries=[...chats.map(x=>({...x,type:'chat'})),...lives.map(x=>({...x,type:'live'}))].sort((a,b)=>Number(b.pinned)-Number(a.pinned)||new Date(b.updated_at)-new Date(a.updated_at));
    const search=field('Search your conversations','search','search');page.append(search);const list=el('div');page.append(list);
    const render=()=>{list.replaceChildren();const query=search.querySelector('input').value.toLowerCase();const filtered=entries.filter(x=>x.title.toLowerCase().includes(query));
      if(!filtered.length){list.append(el('p',query?'No matching conversations.':'No conversations yet. Start a chat to see it here.','card muted'));return;}
      for(const entry of filtered){const card=el('article',undefined,'card history-item');const heading=el('h2');heading.append(link(entry.title,'/m/'+(entry.type==='chat'?'home':'speakwithai')+'?id='+entry.id,''));card.append(heading);
        card.append(el('p',(entry.type==='chat'?'Chat':'Voice')+' · '+entry.message_count+' messages','muted'));
        const actions=el('div',undefined,'row');actions.append(action(entry.pinned?'Unpin':'Pin',async()=>{await api('/api/'+entry.type+'/'+entry.id+'/'+(entry.pinned?'unpin':'pin'),'POST');entry.pinned=!entry.pinned;render();}),action('Delete',async()=>{if(!confirm('Delete this conversation permanently?'))return;await api('/api/'+entry.type+'/'+entry.id,'DELETE');entries.splice(entries.indexOf(entry),1);render();notice('Conversation deleted.');},'danger'));card.append(actions);list.append(card);
      }
    };search.querySelector('input').addEventListener('input',render);render();notice('Your saved chat and voice conversations.');
  }
  async function settingsPage() {
    const settings=await api('/api/settings');
    const appearance=el('section',undefined,'card');appearance.append(el('h2','Appearance'),el('p','Saved on this browser.','muted'));const choices=el('div',undefined,'row');
    for(const mode of ['light','dark']){const button=action(mode==='light'?'Light':'Dark',()=>{window.MaximusTheme.set(mode);update();},'choice grow');button.dataset.mode=mode;choices.append(button);}
    function update(){choices.querySelectorAll('button').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.mode===window.MaximusTheme.get())));}update();appearance.append(choices);page.append(appearance);
    const form=el('form',undefined,'card stack');form.append(el('h2','Assistant preferences'));
    form.append(select('Default model','model',await modelOptions(settings.model),settings.model));
    const temp=field('Creativity (0–1)','temperature','number',settings.temperature);Object.assign(temp.querySelector('input'),{min:'0',max:'1',step:'0.01',required:true});form.append(temp);
    const context=field('Context limit (8–256)','context_limit','number',settings.context_limit);Object.assign(context.querySelector('input'),{min:'8',max:'256',step:'8',required:true});form.append(context);
    const prompt=field('Instructions for MAXIMUS','system_prompt','textarea',settings.system_prompt);prompt.querySelector('textarea').maxLength=12000;form.append(prompt,select('Voice input language','input_language',languages,settings.input_language),select('Response language','output_language',languages,settings.output_language));
    formSubmit(form,'Save preferences',data=>api('/api/settings','PUT',{...data,temperature:Number(data.temperature),context_limit:Number(data.context_limit)}));page.append(form);
    const links=el('section',undefined,'card settings-links');links.append(link('Profile & account','/m/profile-settings',''),link('Models & providers','/m/api-pro',''),link('Download my data','/api/user/export',''),link('Sign out','/logout',''));page.append(links);notice('Preferences sync with your account.');
  }
  async function profilePage() {
    const profile=await api('/api/user/profile');const form=el('form',undefined,'card stack');form.append(el('p',profile.email,'muted'));
    const name=field('Name','name','text',profile.name);Object.assign(name.querySelector('input'),{required:true,maxLength:120,autocomplete:'name'});form.append(name,field('Role','role','text',profile.role));
    const bio=field('About you','bio','textarea',profile.bio);bio.querySelector('textarea').maxLength=240;form.append(bio,field('Timezone','timezone','text',profile.timezone),field('Locale','locale','text',profile.locale));
    formSubmit(form,'Save profile',data=>api('/api/user/profile','PATCH',data));page.append(form,link('Back to settings','/m/settings'));notice('Your account details.');
  }
  async function providersPage() {
    const list=el('div');page.append(el('p','Connect an OpenAI-compatible provider. Keys are sent securely to your backend, not stored in this browser.','muted'),list);
    async function refresh(){const providers=await api('/api/providers');list.replaceChildren();if(!providers.length)list.append(el('p','No custom providers connected. MAXIMUS Flash is still available.','card muted'));
      for(const provider of providers){const card=el('article',undefined,'card');card.append(el('h2',provider.label),el('p',provider.is_valid&&provider.is_active?'Connected':'Unavailable','muted'),el('p',provider.models.join(', ')||'No models registered','muted'));card.append(action('Remove provider',async()=>{if(!confirm('Remove this provider?'))return;await api('/api/providers/'+provider.id,'DELETE');await refresh();},'danger'));list.append(card);}}
    await refresh();const form=el('form',undefined,'card stack');form.append(el('h2','Add a provider'));
    for(const [label,name,type] of [['Name','label','text'],['API base URL','base_url','url'],['API key','api_key','password'],['Model ID','model_id','text']]){const item=field(label,name,type);item.querySelector('input').required=true;if(name==='api_key')item.querySelector('input').autocomplete='off';form.append(item);}
    formSubmit(form,'Test & connect',async data=>{await api('/api/providers','POST',{...data,provider:'custom',api_format:'chat_completions',is_active:true});form.reset();await refresh();});page.append(form);notice('Your API keys are never displayed here.');
  }
  document.querySelector('[data-nav="'+(['profile','providers'].includes(kind)?'settings':kind)+'"]')?.setAttribute('aria-current','page');
  const routes={chat:()=>chat(false),voice:()=>chat(true),history:historyPage,settings:settingsPage,profile:profilePage,providers:providersPage};
  routes[kind]().catch(error=>{notice(error.message,true);page.append(action('Retry loading',()=>location.reload()));});
})();
