import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const root = document.getElementById('imaging-viewer');
const $ = id => document.getElementById(id);
const colors = { unrecorded: '#b5bfc2', healthy: '#e9e5d7', decay: '#ef916d', filled: '#8dcbe4', missing: '#71828c', crown: '#e3c266', treatment: '#c2a0e5' };
const names = { unrecorded: 'Not shared / not recorded', healthy: 'Healthy', decay: 'Decay', filled: 'Filled', missing: 'Missing', crown: 'Crown', treatment: 'Needs treatment' };
let scene, camera, renderer, controls, model, records = new Map(), upper, lower;
let active = false, initialized = false, loading, selected = null, hover = null, down;
let toothGroups = new Map(), pickable = [], buttons = new Map(), frame = 0;
const ray = new THREE.Raycaster(), pointer = new THREE.Vector2();
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

function selectionNumbers() {
  const baby = $('chart-dentition').value === 'primary';
  const quadrants = baby ? [5, 6, 7, 8] : [1, 2, 3, 4];
  const count = baby ? 5 : 8;
  return quadrants.flatMap(q => Array.from({length: count}, (_, i) => q * 10 + i + 1));
}

function describe(number, hoverOnly = false) {
  const record = records.get(number);
  const quadrant = Math.floor(number / 10), position = number % 10;
  const isPrimary = quadrant > 4;
  const name = position === 1 ? 'Central incisor' : position === 2 ? 'Lateral incisor' : position === 3 ? 'Canine' : !isPrimary && position < 6 ? 'Premolar' : 'Molar';
  const side = [1, 4, 5, 8].includes(quadrant) ? 'patient’s right' : 'patient’s left';
  const jaw = [1, 2, 5, 6].includes(quadrant) ? 'Upper' : 'Lower';
  const detail = $('chart-tooth-detail');
  detail.replaceChildren();
  const heading = document.createElement('h3'); heading.textContent = `Tooth ${number} · ${name}`;
  const location = document.createElement('p'); location.className = 'small muted'; location.textContent = `${jaw}, ${side} · ${isPrimary ? 'Primary' : 'Permanent'}`;
  const condition = document.createElement('p'); condition.className = 'tooth-condition-label';
  condition.textContent = record?.condition_label || names.unrecorded;
  condition.style.borderLeftColor = colors[record?.condition || 'unrecorded'];
  const note = document.createElement('p'); note.textContent = record?.notes || (record ? 'No patient-facing notes added.' : 'No shared finding is available for this tooth. A neutral marker does not mean healthy.');
  detail.append(heading, location, condition, note);
  if(number%10===8||isPrimary){const coverage=document.createElement('p');coverage.className='small muted';coverage.textContent='This tooth is not modeled in the anatomical reference. Its recorded findings remain available here.';detail.append(coverage);}
  if (record && !record.shared) {
    const draft = document.createElement('p'); draft.className = 'small muted'; draft.textContent = 'Staff preview: this finding has not been shared with the patient.'; detail.append(draft);
  }
  if (!hoverOnly) {
    selected = number;
    for (const [id, button] of buttons) button.setAttribute('aria-pressed', String(id === number));
    $('chart-tooth-select').value = String(number);
  }
  updateHighlight();
}

function updateHighlight() {
  for (const [number, group] of toothGroups) {
    const highlighted = number === (hover ?? selected);
    group.scale.setScalar(highlighted ? 1.055 : 1);
    group.traverse(child => {
      if (child.isMesh && child.userData.crown) {
        child.material.emissive.set(highlighted ? '#263f3b' : '#000000');
        child.material.emissiveIntensity = highlighted ? .6 : 0;
      }
    });
  }
}

let anatomyParts=[];

async function loadReferenceAnatomy(){
  const manifestURL=new URL(root.dataset.anatomy,location.href);
  const response=await fetch(manifestURL,{credentials:'same-origin'});
  if(!response.ok)throw new Error('The reference anatomy could not load. Use the tooth numbers below.');
  const manifest=await response.json();
  const binaryURL=new URL(manifest.binary,manifestURL);
  if(binaryURL.origin!==location.origin)throw new Error('Invalid reference anatomy location.');
  const binaryResponse=await fetch(binaryURL,{credentials:'same-origin'});
  if(!binaryResponse.ok)throw new Error('The reference anatomy could not load. Use the tooth numbers below.');
  const buffer=await binaryResponse.arrayBuffer();
  anatomyParts=manifest.parts.map(part=>{
    const offset=part.offset,vertexCount=part.vertexCount;
    if(offset+part.bytes>buffer.byteLength)throw new Error('Incomplete reference anatomy.');
    const positions=new Float32Array(buffer,offset+part.positions,vertexCount*3);
    const packedNormals=new Int16Array(buffer,offset+part.normals,vertexCount*3);
    const indices=new Uint32Array(buffer,offset+part.indices,part.indexCount);
    const transformed=new Float32Array(positions.length),normals=new Float32Array(positions.length);
    for(let i=0;i<positions.length;i+=3){
      transformed[i]=positions[i]*100;
      transformed[i+1]=(positions[i+1]-1.55)*100;
      transformed[i+2]=(positions[i+2]-.04)*100;
    }
    for(let i=0;i<normals.length;i++)normals[i]=packedNormals[i]/32767;
    const geometry=new THREE.BufferGeometry();
    geometry.setAttribute('position',new THREE.BufferAttribute(transformed,3));
    geometry.setAttribute('normal',new THREE.BufferAttribute(normals,3));
    geometry.setIndex(new THREE.BufferAttribute(new Uint32Array(indices),1));
    geometry.computeBoundingBox();
    const center=geometry.boundingBox.getCenter(new THREE.Vector3());geometry.translate(-center.x,-center.y,-center.z);
    return {...part,geometry,center};
  });
}

function disposeModel(){
  if(!model)return;
  scene.remove(model);
  model.traverse(object=>{if(object.material)object.material.dispose();});
}

function buildModel(){
  if(!scene||!anatomyParts.length)return;
  disposeModel();model=new THREE.Group();upper=new THREE.Group();lower=new THREE.Group();
  lower.position.y=-.65;model.add(upper,lower);scene.add(model);pickable=[];toothGroups=new Map();
  const primary=$('chart-dentition').value==='primary';
  for(const part of anatomyParts){
    const condition=records.get(part.number)?.condition||'unrecorded';
    const color=part.number?colors[condition]:part.bone?'#dbcca9':'#b9767d';
    const material=new THREE.MeshPhysicalMaterial({color,roughness:part.number?.25:part.bone?.65:.45,
      clearcoat:part.number?.32:.05,clearcoatRoughness:.3,metalness:0,
      transparent:!!part.number&&condition==='missing',opacity:part.number&&condition==='missing'?.13:1,
      clippingPlanes:part.jaw==='upper'&&part.number?[new THREE.Plane(new THREE.Vector3(0,-1,0),0)]:[],clipShadows:true});
    const mesh=new THREE.Mesh(part.geometry,material);mesh.castShadow=true;mesh.receiveShadow=true;
    const group=new THREE.Group();group.position.copy(part.center);group.add(mesh);
    (part.jaw==='upper'?upper:lower).add(group);
    if(part.number){mesh.userData={number:part.number,crown:true};pickable.push(mesh);toothGroups.set(part.number,group);}
  }
  model.visible=!primary;
  $('chart-model-message').textContent=primary
    ?'Primary teeth are not included in this anatomical reference. Read shared findings using the tooth numbers below.'
    :'BodyParts3D anatomical reference: 28 permanent teeth. Hover or tap a tooth. Third molars remain available by number.';
  setJawVisibility();updateHighlight();
}

function setJawVisibility() {
  if(!upper)return;
  const value=$('chart-jaw').value;
  upper.visible=value!=='lower'; lower.visible=value!=='upper';
}

function buildAccessibleList() {
  const grid=$('chart-tooth-list'), select=$('chart-tooth-select'); grid.replaceChildren();select.replaceChildren();buttons=new Map();
  const placeholder=document.createElement('option');placeholder.value='';placeholder.textContent='Choose a tooth';select.append(placeholder);
  for(const number of selectionNumbers()) {
    const record=records.get(number), condition=record?.condition||'unrecorded';
    const button=document.createElement('button');button.type='button';button.textContent=String(number);button.className='tooth-choice';button.style.setProperty('--tooth-color',colors[condition]);
    button.setAttribute('aria-label',`Tooth ${number}: ${record?.condition_label||names.unrecorded}`);button.setAttribute('aria-pressed','false');
    button.addEventListener('click',()=>describe(number));grid.append(button);buttons.set(number,button);
    const option=document.createElement('option');option.value=String(number);option.textContent=`${number} · ${record?.condition_label||names.unrecorded}`;select.append(option);
  }
}

function intersect(event) {
  const box=renderer.domElement.getBoundingClientRect();
  pointer.set((event.clientX-box.left)/box.width*2-1,-(event.clientY-box.top)/box.height*2+1);
  ray.setFromCamera(pointer,camera);
  return ray.intersectObjects(pickable.filter(object=>model.visible&&object.parent.parent.visible),false).find(hit=>![1,2].includes(Math.floor(hit.object.userData.number/10))||hit.point.y<=0)?.object.userData.number;
}

function resetCamera() {
  camera.position.set(10,5,20); controls.target.set(0,-.8,1.2);controls.update();
}

function tick() {
  if(!active||document.hidden){frame=0;return;}
  controls.autoRotate=$('chart-animate').checked&&!reducedMotion.matches;
  controls.update();renderer.render(scene,camera);
  frame=requestAnimationFrame(tick);
}

function activateAnimation() {if(!frame&&active&&renderer)frame=requestAnimationFrame(tick);}

function initScene() {
  const host=$('dental-model-container');
  scene=new THREE.Scene();scene.background=new THREE.Color('#e8eeed');
  camera=new THREE.PerspectiveCamera(38,1,.1,150);
  renderer=new THREE.WebGLRenderer({antialias:true,alpha:false});renderer.setPixelRatio(Math.min(window.devicePixelRatio||1,2));
  renderer.localClippingEnabled=true;renderer.outputColorSpace=THREE.SRGBColorSpace;renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=1.15;renderer.shadowMap.enabled=true;renderer.shadowMap.type=THREE.PCFSoftShadowMap;renderer.domElement.setAttribute('aria-label','Illustrative 3D dental chart. Drag to rotate; use the tooth buttons for keyboard selection.');
  host.append(renderer.domElement);
  scene.add(new THREE.HemisphereLight('#ffffff','#9e9b96',2.1));
  const key=new THREE.DirectionalLight('#fff4df',3);key.position.set(-5,9,9);key.castShadow=true;key.shadow.mapSize.set(2048,2048);key.shadow.camera.left=-8;key.shadow.camera.right=8;key.shadow.camera.top=7;key.shadow.camera.bottom=-7;key.shadow.normalBias=.035;key.shadow.bias=-.0002;scene.add(key);
  const fill=new THREE.DirectionalLight('#dbe9ff',1.4);fill.position.set(6,1,-5);scene.add(fill);const rim=new THREE.DirectionalLight('#fff1e6',2);rim.position.set(2,5,-6);scene.add(rim);
  controls=new OrbitControls(camera,renderer.domElement);controls.enableDamping=true;controls.minDistance=7;controls.maxDistance=35;controls.autoRotateSpeed=.6;controls.enablePan=true;
  resetCamera();buildModel();
  const resize=()=>{const rect=host.getBoundingClientRect();if(rect.width&&rect.height){camera.aspect=rect.width/rect.height;camera.updateProjectionMatrix();renderer.setSize(rect.width,rect.height);}};
  new ResizeObserver(resize).observe(host);resize();
  renderer.domElement.addEventListener('pointerdown',event=>{down=[event.clientX,event.clientY];});
  renderer.domElement.addEventListener('pointermove',event=>{
    if(event.buttons)return;
    const number=intersect(event);hover=number??null;renderer.domElement.style.cursor=number?'pointer':'grab';
    const tooltip=$('chart-tooltip');tooltip.hidden=!number;
    if(number){tooltip.textContent=`Tooth ${number} · ${records.get(number)?.condition_label||names.unrecorded}`;tooltip.style.left=Math.min(event.clientX-host.getBoundingClientRect().left+12,host.clientWidth-180)+'px';tooltip.style.top=Math.max(10,event.clientY-host.getBoundingClientRect().top-40)+'px';}
    updateHighlight();
  });
  renderer.domElement.addEventListener('pointerleave',()=>{hover=null;$('chart-tooltip').hidden=true;updateHighlight();});
  renderer.domElement.addEventListener('pointerup',event=>{if(down&&Math.hypot(event.clientX-down[0],event.clientY-down[1])<7){const number=intersect(event);if(number)describe(number);}down=null;});
  controls.addEventListener('start',()=>{$('chart-animate').checked=false;});
  renderer.domElement.addEventListener('webglcontextlost',event=>{event.preventDefault();active=false;$('chart-model-message').textContent='Graphics support was lost. You can still select teeth using the buttons below.';});
  document.addEventListener('visibilitychange',activateAnimation);
  activateAnimation();
}

async function prepare() {
  if(initialized)return;
  if(loading)return loading;
  loading=(async()=>{
    const response=await fetch(root.dataset.chart,{credentials:'same-origin',cache:'no-store'});
    if(!response.ok||response.redirected)throw new Error('The chart is unavailable. Sign in again or ask the clinic to review document access.');
    const data=await response.json();records=new Map(data.teeth.map(record=>[record.number,record]));
    buildAccessibleList();
    $('chart-model-message').textContent='Illustrative current dental chart. Hover or tap a tooth, or choose its number below.';
    try{await loadReferenceAnatomy();initScene();}catch(error){$('chart-model-message').textContent='The anatomical 3D view is unavailable. Use the tooth numbers below to read shared findings.';}
    initialized=true;
  })();
  try{await loading;}catch(error){$('chart-model-message').textContent=error.message;loading=null;}
}

function mode(next) {
  active=next==='chart';
  $('panel-chart').hidden=!active;$('controls-chart').hidden=!active;
  $('mode-chart').setAttribute('aria-selected',String(active));
  if(active){
    for(const name of ['2d','3d']){$('panel-'+name).hidden=true;$('controls-'+name).hidden=true;$('mode-'+name).setAttribute('aria-selected','false');}
    $('viewer-status').hidden=true;
    if($('contrast'))$('contrast').closest('.scan-only-controls').hidden=true;
    root.dispatchEvent(new CustomEvent('dentech:chart-mode',{detail:true}));
    prepare().then(activateAnimation);
  }else{
    $('viewer-status').hidden=false;
    if($('contrast'))$('contrast').closest('.scan-only-controls').hidden=false;
    if(root.dataset.kind==='pdf'&&next==='2d'){$('panel-2d').hidden=false;$('controls-2d').hidden=false;$('mode-2d').setAttribute('aria-selected','true');}
  }
}

if(root&&$('mode-chart')) {
  $('mode-chart').addEventListener('click',()=>mode('chart'));
  $('mode-2d').addEventListener('click',()=>mode('2d'));
  $('mode-3d').addEventListener('click',()=>mode('3d'));
  $('chart-dentition').addEventListener('change',()=>{selected=null;hover=null;$('chart-tooltip').hidden=true;buildAccessibleList();buildModel();$('chart-tooth-detail').textContent='Select a tooth to read its recorded finding.';});
  $('chart-jaw').addEventListener('change',setJawVisibility);
  $('chart-tooth-select').addEventListener('change',event=>{if(event.target.value)describe(Number(event.target.value));});
  $('chart-reset').addEventListener('click',()=>{if(camera && controls)resetCamera();$('chart-animate').checked=false;});
  $('chart-animate').addEventListener('change',activateAnimation);
  if(reducedMotion.matches){$('chart-animate').disabled=true;$('chart-animate').title='Animation is disabled to respect your reduced-motion preference.';}
}
