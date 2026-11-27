// The psychic room's crystal ball: orb-ui's <Orb> in controlled mode. The page
// owns the conversation (the browser listens and speaks, /api/message answers),
// so the orb is only told what state it is in and how loud each side is.
import React from 'react';
import {createRoot} from 'react-dom/client';
import {Orb} from 'orb-ui';

// Each crystal is a cloud-theme palette. Kept here so the page only names one.
const CRYSTALS = {
  amethyst:  {deepColor: '#1a0b33', upperColor: '#9b6bff', lowerColor: '#4b2a99', highlightColor: '#f1e6ff', launchColor: '#c9a8ff', spinnerColor: '#e5d4ff'},
  moonstone: {deepColor: '#0b1426', upperColor: '#a9c4ff', lowerColor: '#3d5a99', highlightColor: '#f4f8ff', launchColor: '#cfe0ff', spinnerColor: '#eaf1ff'},
  ember:     {deepColor: '#2a0a05', upperColor: '#ff9a5a', lowerColor: '#a8321a', highlightColor: '#fff0dc', launchColor: '#ffc08a', spinnerColor: '#ffe2c4'},
  jade:      {deepColor: '#04201a', upperColor: '#5fe0b4', lowerColor: '#16775c', highlightColor: '#e6fff5', launchColor: '#a4f0d4', spinnerColor: '#d4fff0'},
  rose:      {deepColor: '#2a0618', upperColor: '#ff8cc6', lowerColor: '#a12a6a', highlightColor: '#fff0f7', launchColor: '#ffb8dc', spinnerColor: '#ffe0f0'},
  opal:      {deepColor: '#161a24', upperColor: '#e8f0ff', lowerColor: '#8fa3c8', highlightColor: '#ffffff', launchColor: '#f2f6ff', spinnerColor: '#ffffff'},
  gold:      {deepColor: '#241703', upperColor: '#ffd36b', lowerColor: '#a8741a', highlightColor: '#fff6da', launchColor: '#ffe29a', spinnerColor: '#fff0c4'},
  aqua:      {deepColor: '#03202a', upperColor: '#5fd6f0', lowerColor: '#16708c', highlightColor: '#e6fbff', launchColor: '#a4ecfa', spinnerColor: '#d4f8ff'},
  obsidian:  {deepColor: '#050507', upperColor: '#6c6f86', lowerColor: '#23242e', highlightColor: '#d9dcf0', launchColor: '#9a9db4', spinnerColor: '#c4c7da'},
  blood:     {deepColor: '#1c0206', upperColor: '#ff4d5e', lowerColor: '#8a0f1f', highlightColor: '#ffe3e6', launchColor: '#ff8f9a', spinnerColor: '#ffc8cd'},
};

// Her own colour: any #rrggbb becomes a palette (a dark, a mid, the colour, and lights).
const mix = (hex, to, k) => '#' + [0, 2, 4].map(i => {
  const a = parseInt(hex.slice(1 + i, 3 + i), 16), b = parseInt(to.slice(1 + i, 3 + i), 16);
  return Math.round(a + (b - a) * k).toString(16).padStart(2, '0'); }).join('');
const custom = hex => ({deepColor: mix(hex, '#000000', .86), upperColor: hex, lowerColor: mix(hex, '#000000', .45),
  highlightColor: mix(hex, '#ffffff', .85), launchColor: mix(hex, '#ffffff', .4), spinnerColor: mix(hex, '#ffffff', .65)});

function theme(crystal, style){
  const appearance = /^#[0-9a-f]{6}$/i.test(crystal || '') ? custom(crystal) : CRYSTALS[crystal] || CRYSTALS.amethyst;
  if (style === 'radial') {
    return {name: 'radial', preset: 'calm', appearance: {
      deepColor: appearance.deepColor, cobaltColor: appearance.lowerColor, aquaColor: appearance.upperColor,
      paleColor: appearance.highlightColor, membraneColor: appearance.launchColor, seamColor: appearance.spinnerColor},
      // The swirl is drawn as full as the cloud, so the ball keeps its size whichever way it moves.
      geometry: {diameterRatio: 0.92}};
  }
  // A crystal ball never shrinks to a dot: keep it whole while idle.
  return {name: 'cloud', preset: style === 'lively' ? 'expressive' : 'calm', appearance,
          geometry: {idleDotScale: 1, diameterRatio: 0.92}};
}

window.PsychicOrb = {
  crystals: Object.keys(CRYSTALS),
  mount(el){
    const root = createRoot(el);
    let props = {state: 'idle', inputVolume: 0, outputVolume: 0, crystal: 'amethyst', style: 'calm', size: 240};
    const draw = () => root.render(
      <Orb theme={theme(props.crystal, props.style)} size={props.size} interactive={false}
           aria-label="Crystal ball"
           signal={{state: props.state, inputVolume: props.inputVolume, outputVolume: props.outputVolume}} />);
    draw();
    return {
      set(next){ props = {...props, ...next}; draw(); },
      unmount(){ root.unmount(); },
    };
  },
};
