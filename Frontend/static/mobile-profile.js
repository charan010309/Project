'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const node = (tag,text,cls='') => {const e=document.createElement(tag);if(text!==undefined)e.textContent=text;e.className=cls;return e;};
  let profile=null, avatar, busy=false, modal=null, returnFocus=null, sentEmail='', photoVersion=0, otherDevices=false;
  const fields={'name':'profile-name-input','role':'profile-role-input','bio':'bio-input','timezone':'profile-timezone','locale':'profile-locale'};
  function status(message=''){ $('profile-status').textContent=message; }
  async function api(path,method='GET',data){
    const response=await fetch(path,{method,credentials:'same-origin',headers:data?{'Content-Type':'application/json'}:{},body:data?JSON.stringify(data):undefined});
    if(response.status===401){location.assign('/m/login');throw Error('Please sign in again.');}
    const result=await response.json().catch(()=>({}));
    if(!response.ok)throw Error(typeof result.detail==='string'?result.detail:'Request failed. Please try again.');
    return result;
  }
  function setBusy(value){busy=value;document.querySelectorAll('button,input,select,textarea').forEach(e=>{e.disabled=value;});$('drawer-toggle').disabled=false;$('revoke-others').disabled=value||!otherDevices;}
  function selectValue(id,value){const select=$(id);if(![...select.options].some(o=>o.value===value)){const option=node('option',value);option.value=value;select.append(option);}select.value=value;}
  function renderPhoto(value){const image=$('profile-photo');const valid=typeof value==='string'&&/^data:image\/(png|jpeg|webp);base64,/.test(value);image.classList.toggle('hidden',!valid);$('avatar-initials').classList.toggle('hidden',valid);if(valid)image.src=value;else image.removeAttribute('src');}
  function identity(){
    $('profile-name').textContent=$('drawer-name').textContent=profile.name||'Account';
    $('profile-email').textContent=$('drawer-email').textContent=profile.email;
    $('profile-account-id').textContent=$('drawer-account-id').textContent='Account #'+profile.id;
    $('profile-role').textContent=profile.role||'MAXIMUS member';
    $('avatar-initials').textContent=(profile.name||'M').trim().split(/\s+/).slice(0,2).map(s=>s[0]).join('').toUpperCase();
    $('verification-status').textContent=profile.is_verified?'EMAIL VERIFIED':'EMAIL UNVERIFIED';
    $('email-verified').textContent=profile.is_verified?'Verified':'Unverified';
    $('verified-icon').classList.toggle('hidden',!profile.is_verified);
    $('login-method').textContent=profile.has_password?'PASSWORD SET':'SOCIAL SIGN-IN';
    $('password-summary').textContent=profile.has_password?'Password is configured.':'No MAXIMUS password set. You can create one.';
    $('update-password').textContent=profile.has_password?'Update Password':'Set Password';
  }
  function restore(){if(!profile)return;photoVersion++;avatar=undefined;for(const [key,id]of Object.entries(fields)){if($(id).tagName==='SELECT')selectValue(id,profile[key]||'');else $(id).value=profile[key]||'';}$('weekly-digest').checked=!!profile.weekly_digest;$('avatar-upload').value='';renderPhoto(profile.icon_url);identity();counter();}
  function counter(){$('char-counter').textContent=$('bio-input').value.length+' / 240 chars';}
  function dirty(){return profile&&(avatar!==undefined||Object.entries(fields).some(([k,id])=>$(id).value!==(profile[k]||''))||$('weekly-digest').checked!==!!profile.weekly_digest);}
  async function load(){setBusy(true);status('Loading profile…');$('retry-profile').classList.add('hidden');try{profile=await api('/api/user/profile');restore();status('Account loaded. Changes save when you tap Save Changes.');}catch(e){status(e.message);$('retry-profile').classList.remove('hidden');}finally{setBusy(false);if(!profile){$('btn-save-changes').disabled=true;$('btn-discard').disabled=true;}}}
  $('retry-profile').onclick=load;
  $('bio-input').oninput=counter;
  $('btn-discard').onclick=()=>{if(!busy){restore();status('Unsaved profile changes discarded.');}};
  $('btn-save-changes').onclick=async()=>{
    if(busy||!profile)return;
    if(!$('profile-name-input').reportValidity()||!$('profile-name-input').value.trim()){status('Please enter your name.');return;}
    const data=Object.fromEntries(Object.entries(fields).map(([key,id])=>[key,$(id).value]));data.weekly_digest=$('weekly-digest').checked;
    if(typeof avatar==='string')data.avatar_data=avatar;
    setBusy(true);status('Saving…');
    try{
      profile=await api('/api/user/profile','PATCH',data);
      if(avatar===null){try{await api('/api/user/profile/avatar','DELETE');profile.icon_url=null;}catch(e){identity();status('Profile saved, but photo removal failed: '+e.message);return;}}
      restore();status('Profile saved successfully.');
    }catch(e){status(e.message+' Your unsaved changes are still here.');}finally{setBusy(false);}
  };
  $('change-photo').onclick=$('btn-quick-photo').onclick=()=>{if(profile&&!busy)$('avatar-upload').click();};
  $('remove-photo').onclick=()=>{if(profile&&!busy){photoVersion++;avatar=null;renderPhoto(null);status('Photo will be removed when you save.');}};
  $('avatar-upload').onchange=async()=>{
    const file=$('avatar-upload').files[0];if(!file)return;
    const token=++photoVersion;
    if(!['image/png','image/jpeg','image/webp'].includes(file.type)||file.size>5000000){status('Choose a PNG, JPEG or WebP photo under 5 MB.');$('avatar-upload').value='';return;}
    setBusy(true);status('Preparing photo…');
    try{const bitmap=await createImageBitmap(file);const scale=Math.min(1,512/Math.max(bitmap.width,bitmap.height));const canvas=document.createElement('canvas');canvas.width=Math.max(1,Math.round(bitmap.width*scale));canvas.height=Math.max(1,Math.round(bitmap.height*scale));canvas.getContext('2d').drawImage(bitmap,0,0,canvas.width,canvas.height);bitmap.close();if(token!==photoVersion)return;avatar=canvas.toDataURL('image/png');renderPhoto(avatar);status('Photo ready. Tap Save Changes to upload it.');}catch{status('This image could not be read. Choose another photo.');}finally{setBusy(false);$('avatar-upload').value='';}
  };
  // Modals are direct body children so the background can be made inert safely.
  for(const id of ['modal-email','modal-deactivate'])document.body.append($(id));
  const passwordBox=node('div',undefined,'profile-modal hidden');passwordBox.id='modal-password';
  const passwordPanel=node('div',undefined,'profile-panel');passwordPanel.append(node('h3','Password','text-headline-md font-semibold'));
  function input(label,id,type='text'){const wrap=node('label',label,'profile-label');const field=node('input');field.id=id;field.type=type;field.className='profile-field';field.setAttribute('aria-label',label);wrap.append(field);return wrap;}
  passwordPanel.append(input('Current password','current-password','password'),input('New password','new-password','password'),input('Confirm new password','confirm-password','password'));
  $('modal-email').firstElementChild.insertBefore(input('Verification code','email-otp'),$('modal-email').firstElementChild.lastElementChild);
  $('email-otp').inputMode='numeric';$('email-otp').autocomplete='one-time-code';$('email-otp').maxLength=6;$('email-otp').parentElement.hidden=true;
  const emailStatus=node('p','','text-body-sm');emailStatus.id='email-status';emailStatus.setAttribute('role','status');$('modal-email').firstElementChild.append(emailStatus);
  const verify=node('button','Verify email','profile-primary');verify.id='verify-email';verify.hidden=true;$('modal-email').firstElementChild.append(verify);
  const passwordStatus=node('p');passwordStatus.id='password-status';passwordStatus.setAttribute('role','status');passwordPanel.append(passwordStatus);
  const passwordActions=node('div',undefined,'flex gap-2 justify-end');const cancel=node('button','Cancel','profile-secondary');cancel.onclick=()=>close();const savePassword=node('button','Save password','profile-primary');savePassword.id='save-password';passwordActions.append(cancel,savePassword);passwordPanel.append(passwordActions);passwordBox.append(passwordPanel);document.body.append(passwordBox);
  $('current-password').autocomplete='current-password';for(const id of ['new-password','confirm-password']){$(id).autocomplete='new-password';$(id).minLength=8;$(id).maxLength=256;}
  const confirmBox=node('div',undefined,'profile-modal hidden');confirmBox.id='modal-revoke';const confirmPanel=node('div',undefined,'profile-panel');confirmPanel.append(node('h3','Revoke session?','text-headline-md'),node('p','The selected device will need to sign in again.'));const revokeStatus=node('p');revokeStatus.id='revoke-status';revokeStatus.setAttribute('role','status');confirmPanel.append(revokeStatus);const revokeCancel=node('button','Cancel','profile-secondary');revokeCancel.onclick=()=>close();const revokeConfirm=node('button','Revoke session','profile-primary');confirmPanel.append(revokeCancel,revokeConfirm);confirmBox.append(confirmPanel);document.body.append(confirmBox);
  let revokePath='';
  function open(id){if(busy||!profile)return;close();returnFocus=document.activeElement;modal=$(id);modal.classList.remove('hidden','opacity-0');modal.firstElementChild.classList.remove('translate-y-4');modal.setAttribute('role','dialog');modal.setAttribute('aria-modal','true');modal.setAttribute('aria-label',id==='modal-email'?'Change email':id==='modal-password'?'Change password':id==='modal-revoke'?'Revoke sessions':'Delete account');for(const e of document.body.children)if(e!==modal)e.inert=true;modal.inert=false;document.body.style.overflow='hidden';[...modal.querySelectorAll('input,button')].find(e=>e.getClientRects().length&&!e.disabled)?.focus();}
  function close(){if(busy)return;if(modal){modal.classList.add('hidden');modal.inert=true;modal=null;}for(const e of document.body.children)e.inert=false;$('nav-drawer').inert=!$('drawer-toggle').checked;$('drawer-backdrop').inert=!$('drawer-toggle').checked;document.body.style.overflow='';if(returnFocus?.isConnected)returnFocus.focus();}
  $('btn-open-email-modal').onclick=()=>{sentEmail='';$('email-status').textContent='';$('new-email').value='';$('email-otp').value='';$('email-otp').parentElement.hidden=true;verify.hidden=true;open('modal-email');};
  $('btn-close-email-modal').onclick=$('btn-cancel-email').onclick=close;
  $('new-email').oninput=()=>{sentEmail='';verify.hidden=true;$('email-otp').parentElement.hidden=true;};
  $('send-email-code').onclick=async()=>{if(busy||!$('new-email').reportValidity())return;setBusy(true);$('email-status').textContent='Sending code…';try{const email=$('new-email').value.trim();await api('/api/user/email/request','POST',{new_email:email});sentEmail=email;$('email-otp').parentElement.hidden=false;verify.hidden=false;$('email-status').textContent='Code sent. Enter it below. Codes expire after 10 minutes.';}catch(e){$('email-status').textContent=e.message;}finally{setBusy(false);}};
  verify.onclick=async()=>{if(busy||!sentEmail)return;if(!/^\d{6}$/.test($('email-otp').value)){$('email-status').textContent='Enter the 6-digit code.';return;}setBusy(true);try{const result=await api('/api/user/email/verify','POST',{new_email:sentEmail,otp:$('email-otp').value});profile.email=result.email;profile.is_verified=true;identity();setBusy(false);close();status('Email updated successfully.');}catch(e){$('email-status').textContent=e.message;}finally{setBusy(false);}};
  $('update-password').onclick=()=>{for(const id of ['current-password','new-password','confirm-password'])$(id).value='';$('current-password').parentElement.hidden=!profile?.has_password;$('password-status').textContent='Use at least 8 characters.';open('modal-password');};
  savePassword.onclick=async()=>{if(busy||!profile)return;const next=$('new-password').value;if(next.length<8||next!==$('confirm-password').value){$('password-status').textContent='Use at least 8 characters and make sure both new passwords match.';return;}setBusy(true);try{await api('/api/user/password'+(profile.has_password?'':'/set'),'POST',{current_password:$('current-password').value,new_password:next});profile.has_password=true;identity();for(const id of ['current-password','new-password','confirm-password'])$(id).value='';setBusy(false);close();status('Password saved.');}catch(e){$('password-status').textContent=e.message;}finally{setBusy(false);}};
  async function devices(){ $('retry-devices').classList.add('hidden');try{const list=await api('/api/devices');$('session-count').textContent=list.length+' ACTIVE';$('device-list').replaceChildren();if(!list.length)$('device-list').append(node('p','No registered device sessions.','text-body-sm'));for(const d of list){const row=node('div',undefined,'p-3 rounded-lg bg-surface-container-low space-y-2');row.append(node('h4',d.name||'Device','font-semibold'),node('p',d.current?'This device':d.online?'Recently active':'Last seen: '+(d.last_seen_at?new Date(d.last_seen_at+'Z').toLocaleString():'Unavailable'),'text-body-sm'));if(d.ip_address)row.append(node('p','IP: '+d.ip_address,'text-code-block'));const b=node('button',d.current?'Sign out this device':'Revoke Session','profile-secondary');b.onclick=()=>askRevoke('/api/devices/'+d.id+'/revoke');row.append(b);$('device-list').append(row);}otherDevices=list.some(d=>!d.current);$('revoke-others').disabled=!otherDevices;}catch(e){$('session-count').textContent='Unavailable';$('device-list').replaceChildren(node('p',e.message));$('retry-devices').classList.remove('hidden');}}
  function askRevoke(path){revokePath=path;$('revoke-status').textContent='';open('modal-revoke');}
  $('retry-devices').onclick=devices;$('revoke-others').onclick=()=>askRevoke('/api/devices/revoke-others');
  revokeConfirm.onclick=async()=>{if(busy)return;setBusy(true);try{const r=await api(revokePath,'POST');if(r.logged_out){location.assign('/m/login');return;}setBusy(false);close();status(r.message);await devices();}catch(e){$('revoke-status').textContent=e.message;}finally{setBusy(false);}};
  $('export-profile').onclick=async()=>{if(busy)return;setBusy(true);try{const data=await api('/api/user/export');const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const a=node('a');a.href=url;a.download='maximus-account-export.json';document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);status('Account export downloaded.');}catch(e){status(e.message);}finally{setBusy(false);}};
  $('btn-open-deactivate-modal').onclick=()=>{$('delete-confirmation').value='';$('delete-status').textContent='';open('modal-deactivate');};$('btn-cancel-deactivate').onclick=close;
  $('delete-account').onclick=async()=>{if(busy)return;if($('delete-confirmation').value!=='DELETE'){$('delete-status').textContent='Type DELETE to confirm permanent deletion.';return;}setBusy(true);try{await api('/api/account','DELETE');profile=null;location.assign('/m/signup');}catch(e){$('delete-status').textContent=e.message;}finally{setBusy(false);}};
  const drawer=$('nav-drawer');drawer.inert=true;$('drawer-backdrop').inert=true;$('toast').hidden=true;
  for(const label of document.querySelectorAll('label[for="drawer-toggle"]')){label.tabIndex=0;label.setAttribute('role','button');label.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();label.click();}};}
  $('drawer-toggle').onchange=()=>{const visible=$('drawer-toggle').checked;drawer.inert=!visible;$('drawer-backdrop').inert=!visible;document.querySelector('main').inert=visible;document.querySelector('header').inert=visible;document.body.style.overflow=visible?'hidden':'';if(visible){returnFocus=document.activeElement;drawer.querySelector('[aria-label="Close drawer"]').focus();}else{returnFocus?.focus();}};
  document.addEventListener('keydown',e=>{const active=modal||($('drawer-toggle').checked?drawer:null);if(e.key==='Escape'&&!busy){if(modal)close();else if($('drawer-toggle').checked){$('drawer-toggle').checked=false;$('drawer-toggle').onchange();}}if(active&&e.key==='Tab'){const controls=[...active.querySelectorAll('button,a[href],input,select,textarea,[tabindex="0"]')].filter(x=>!x.disabled&&x.getClientRects().length);const first=controls[0],last=controls.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus();}}});
  window.addEventListener('beforeunload',e=>{if(dirty()){e.preventDefault();e.returnValue='';}});
  async function start(){await load();if(profile)await devices();}start();
})();
