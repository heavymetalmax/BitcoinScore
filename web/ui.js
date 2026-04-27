// Utilities for persisting slider state in localStorage
function saveSliders(key, state) {
  try {
    localStorage.setItem(key, JSON.stringify(state));
  } catch (e) {
    console.warn('Failed to save sliders', e);
  }
}

function loadSliders(key) {
  try {
    const s = localStorage.getItem(key);
    return s ? JSON.parse(s) : null;
  } catch (e) {
    console.warn('Failed to load sliders', e);
    return null;
  }
}

function resetSliders(key, defaults = null) {
  try {
    localStorage.removeItem(key);
    if (defaults) saveSliders(key, defaults);
  } catch (e) {
    console.warn('Failed to reset sliders', e);
  }
}

export { saveSliders, loadSliders, resetSliders };