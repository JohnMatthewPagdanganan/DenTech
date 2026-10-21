import '@kitware/vtk.js/Rendering/Profiles/Volume';
import vtkGenericRenderWindow from '@kitware/vtk.js/Rendering/Misc/GenericRenderWindow';
import vtkImageData from '@kitware/vtk.js/Common/DataModel/ImageData';
import vtkDataArray from '@kitware/vtk.js/Common/Core/DataArray';
import vtkVolume from '@kitware/vtk.js/Rendering/Core/Volume';
import vtkVolumeMapper from '@kitware/vtk.js/Rendering/Core/VolumeMapper';
import vtkColorTransferFunction from '@kitware/vtk.js/Rendering/Core/ColorTransferFunction';
import vtkPiecewiseFunction from '@kitware/vtk.js/Common/DataModel/PiecewiseFunction';

const root = document.getElementById('imaging-viewer');
const $ = id => document.getElementById(id);
const kind = root?.dataset.kind;
let meta, values, image, rendering, mapper, actor, resize3d;
let mode = '2d', angle = 0, pan = [0, 0], dragging = false, previous;
let plane = document.createElement('canvas');
let zoom = 1, volumeReady = false, renderingFailed = false;
const status = text => { $('viewer-status').textContent = text; };
async function get(url, binary = false) {
  const response = await fetch(url, { credentials: 'same-origin', cache: 'no-store' });
  if (!response.ok || response.redirected) throw new Error('Your session may have expired or this file is no longer available. Sign in again or contact the clinic.');
  return binary ? response.arrayBuffer() : response.json();
}

function paintSlice() {
  if (!meta || !values) return;
  const axis = Number($('slice-axis').value);
  const [nx, ny, nz] = meta.dimensions;
  const index = Math.min(Number($('slice-index').value), meta.dimensions[axis] - 1);
  const [width, height] = axis === 2 ? [nx, ny] : axis === 1 ? [nx, nz] : [ny, nz];
  plane.width = width; plane.height = height;
  const context = plane.getContext('2d');
  const pixels = context.createImageData(width, height);
  const center = (meta.window[0] + meta.window[1]) / 2;
  const range = (meta.window[1] - meta.window[0]) * 100 / Number($('contrast').value);
  const low = center - range / 2;
  for (let row = 0; row < height; row++) {
    for (let col = 0; col < width; col++) {
      const x = axis === 0 ? index : col;
      const y = axis === 2 ? row : axis === 1 ? index : col;
      const z = axis === 2 ? index : row;
      let gray = Math.round(Math.min(255, Math.max(0, (values[x + nx * (y + ny * z)] - low) / range * 255)));
      if (meta.photometric === 'MONOCHROME1') gray = 255 - gray;
      const offset = 4 * (col + width * row);
      pixels.data[offset] = gray; pixels.data[offset + 1] = gray; pixels.data[offset + 2] = gray; pixels.data[offset + 3] = 255;
    }
  }
  context.putImageData(pixels, 0, 0);
  $('slice-label').textContent = `${index + 1} / ${meta.dimensions[axis]}`;
  draw2d();
}

function draw2d() {
  const canvas = $('slice-canvas');
  if (!canvas || !volumeReady) return;
  const box = canvas.parentElement.getBoundingClientRect();
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = Math.max(1, Math.floor(box.width * dpr));
  canvas.height = Math.max(1, Math.floor(box.height * dpr));
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#101f22'; ctx.fillRect(0, 0, canvas.width, canvas.height);
  const target = kind === 'cbct' ? plane : image;
  if (!target) return;
  let physical = [target.width, target.height];
  if (meta) {
    const axis = Number($('slice-axis').value);
    const [sx, sy, sz] = meta.spacing;
    physical = axis === 2 ? [plane.width * sx, plane.height * sy] : axis === 1 ? [plane.width * sx, plane.height * sz] : [plane.width * sy, plane.height * sz];
  }
  const swapped = Math.abs(angle % 180) === 90;
  const fit = Math.min(canvas.width * .9 / physical[swapped ? 1 : 0], canvas.height * .9 / physical[swapped ? 0 : 1]) * zoom;
  ctx.save();
  ctx.translate(canvas.width / 2 + pan[0] * dpr, canvas.height / 2 + pan[1] * dpr);
  ctx.rotate(angle * Math.PI / 180);
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(target, -physical[0] * fit / 2, -physical[1] * fit / 2, physical[0] * fit, physical[1] * fit);
  ctx.restore();
}

function reset2d() {
  zoom = 1; angle = 0; pan = [0, 0]; $('zoom-2d').value = 100; draw2d();
}

function updateVolumeAppearance() {
  if (!actor || !meta) return;
  const min = meta.range[0], max = meta.range[1];
  const contrast = Number($('contrast').value) / 100;
  const threshold = min + (max - min) * Number($('density').value) / 100;
  const upper = Math.min(max, threshold + (max - min) * .45 / contrast);
  const opacity = Number($('opacity').value) / 100;
  const colors = vtkColorTransferFunction.newInstance();
  colors.addRGBPoint(min, 0, 0, 0);
  colors.addRGBPoint(threshold, .45, .29, .17);
  colors.addRGBPoint(threshold + (upper - threshold) * .45, .87, .79, .61);
  colors.addRGBPoint(upper, 1, .99, .91);
  const alpha = vtkPiecewiseFunction.newInstance();
  alpha.addPoint(min, 0);
  alpha.addPoint(threshold, 0);
  alpha.addPoint(Math.min(max, threshold + (max - min) * .08), opacity * .25);
  alpha.addPoint(upper, opacity);
  const property = actor.getProperty();
  property.setRGBTransferFunction(0, colors);
  property.setScalarOpacity(0, alpha);
  property.setScalarOpacityUnitDistance(0, Math.min(...meta.spacing));
  property.setInterpolationTypeToLinear();
  property.setShade(true); property.setAmbient(.3); property.setDiffuse(.7); property.setSpecular(.2);
  rendering.getRenderWindow().render();
}

function init3d() {
  if (rendering) { rendering.resize(); return; }
  const probe = document.createElement('canvas');
  if (!probe.getContext('webgl2')) throw new Error('3D requires WebGL 2 graphics support. Your 2D slices and original download are still available.');
  rendering = vtkGenericRenderWindow.newInstance({ background: [.055, .10, .115] });
  rendering.setContainer($('volume-container'));
  const dataset = vtkImageData.newInstance();
  dataset.setDimensions(...meta.dimensions);
  dataset.setSpacing(...meta.spacing);
  dataset.setOrigin(...meta.origin);
  dataset.setDirection(meta.direction);
  dataset.getPointData().setScalars(vtkDataArray.newInstance({ name: 'Scan intensity', values, numberOfComponents: 1 }));
  mapper = vtkVolumeMapper.newInstance();
  mapper.setInputData(dataset);
  mapper.setSampleDistance(Math.min(...meta.spacing) * .8);
  actor = vtkVolume.newInstance(); actor.setMapper(mapper);
  const renderer = rendering.getRenderer();
  renderer.addVolume(actor);
  resetCamera();
  rendering.resize();
  updateVolumeAppearance();
  resize3d = new ResizeObserver(() => { if (mode === '3d') rendering.resize(); });
  resize3d.observe($('volume-container'));
  $('volume-container').addEventListener('webglcontextlost', event => {
    event.preventDefault();
    renderingFailed = true;
    switchMode('2d');
    status('Graphics memory was lost. Use 2D slices, or reload to retry 3D on a more capable device.');
  }, true);
}

function resetCamera() {
  const renderer = rendering.getRenderer();
  const bounds = actor.getBounds();
  const center = [(bounds[0] + bounds[1]) / 2, (bounds[2] + bounds[3]) / 2, (bounds[4] + bounds[5]) / 2];
  const extent = Math.max(bounds[1] - bounds[0], bounds[3] - bounds[2], bounds[5] - bounds[4]);
  const camera = renderer.getActiveCamera();
  camera.setFocalPoint(...center);
  camera.setPosition(center[0] + extent * .5, center[1] - extent * 1.4, center[2] + extent * .15);
  camera.setViewUp(0, 0, 1);
  renderer.resetCamera();
  renderer.resetCameraClippingRange();
}

function switchMode(next) {
  if (next === '3d' && (!volumeReady || kind !== 'cbct')) return;
  mode = next;
  for (const name of ['2d', '3d']) {
    const selected = name === next;
    $('mode-' + name).setAttribute('aria-selected', String(selected));
    $('panel-' + name).hidden = !selected;
    $('controls-' + name).hidden = !selected;
  }
  if (next === '3d') {
    try {
      if (renderingFailed) throw new Error('Reload to retry 3D, or continue using 2D slices.');
      init3d();
      status('3D volume ready. Drag to rotate; scroll or pinch to zoom.');
    } catch (error) {
      renderingFailed = true;
      switchMode('2d');
      status(error.message || '3D could not load. You can still explore the 2D slices.');
    }
  } else {
    draw2d();
    status(kind === 'cbct' ? '2D scan slices. Choose a cross-section and move the slice slider.' : '2D image. Drag to pan; use the controls to zoom or rotate.');
  }
}

root?.addEventListener('dentech:chart-mode', () => { mode = 'chart'; });
async function start() {
  if (!root || kind === 'pdf') return;
  const canvas = $('slice-canvas');
  if (kind === 'cbct') {
    meta = await get(root.dataset.metadata);
    const raw = await get(root.dataset.volume, true);
    const expected = meta.dimensions.reduce((a, b) => a * b, 1);
    if (raw.byteLength !== expected * 4) throw new Error('The preview is incomplete. Ask the clinic to prepare the scan again.');
    values = new Float32Array(raw);
    volumeReady = true;
    $('slice-index').max = meta.dimensions[2] - 1;
    $('slice-index').value = Math.floor(meta.dimensions[2] / 2);
    $('scan-details').textContent = `${meta.source_dimensions.join(' × ')} source voxels. ${meta.dimensions.join(' × ')} browser preview. ${meta.downsampled ? 'Reduced resolution for browser performance.' : 'Native voxel sampling.'} Cross-sections follow the scan axes.`;
    $('slice-axis').addEventListener('change', () => {
      const axis = Number($('slice-axis').value);
      $('slice-index').max = meta.dimensions[axis] - 1;
      $('slice-index').value = Math.floor(meta.dimensions[axis] / 2);
      reset2d(); paintSlice();
    });
    $('slice-index').addEventListener('input', paintSlice);
    $('contrast').addEventListener('input', () => { paintSlice(); if (actor) updateVolumeAppearance(); });
    $('density').addEventListener('input', updateVolumeAppearance);
    $('opacity').addEventListener('input', updateVolumeAppearance);
    $('reset-3d').addEventListener('click', () => {
      $('density').value = 40; $('opacity').value = 55; $('contrast').value = 100;
      resetCamera(); updateVolumeAppearance();
    });
    paintSlice();
  } else {
    image = new Image();
    await new Promise((resolve, reject) => {
      image.onload = resolve;
      image.onerror = () => reject(new Error('The image could not load. Download the original or contact the clinic.'));
      image.src = root.dataset.content;
    });
    volumeReady = true; draw2d();
  }
  $('zoom-2d').addEventListener('input', () => { zoom = Number($('zoom-2d').value) / 100; draw2d(); });
  $('rotate-image').addEventListener('click', () => { angle = (angle + 90) % 360; draw2d(); });
  $('reset-2d').addEventListener('click', reset2d);
  $('mode-2d').addEventListener('click', () => switchMode('2d'));
  $('mode-3d').addEventListener('click', () => switchMode('3d'));
  canvas.addEventListener('pointerdown', event => { dragging = true; previous = [event.clientX, event.clientY]; canvas.setPointerCapture(event.pointerId); });
  canvas.addEventListener('pointermove', event => {
    if (!dragging) return;
    pan[0] += event.clientX - previous[0]; pan[1] += event.clientY - previous[1];
    previous = [event.clientX, event.clientY]; draw2d();
  });
  canvas.addEventListener('pointerup', () => { dragging = false; });
  canvas.addEventListener('pointercancel', () => { dragging = false; });
  canvas.addEventListener('wheel', event => {
    event.preventDefault(); zoom = Math.min(4, Math.max(.5, zoom * (event.deltaY < 0 ? 1.1 : .9)));
    $('zoom-2d').value = zoom * 100; draw2d();
  }, { passive: false });
  new ResizeObserver(draw2d).observe(canvas.parentElement);
  if ($('mode-chart')?.getAttribute('aria-selected') !== 'true') switchMode('2d');
}
start().catch(error => status(error.message || 'Unable to load this preview. Download the original or contact the clinic.'));
