// Minimal app loader: fetch data.json and expose to the page
async function loadDataJson(path){
  try{
    const r = await fetch(path,{cache:'no-store'});
    if(!r.ok) throw new Error('HTTP '+r.status);
    return await r.json();
  }catch(e){
    console.warn('Failed to load data.json', e);
    return null;
  }
}

// Example usage:
// loadDataJson('../data/data.json').then(data=>console.log(data));
