async function apiPost(url, payload){
  const res = await fetch(url, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload || {})
  });
  return res.json();
}

async function createLocation(id, description=''){
  return apiPost('/api/locations/create', {location_id: id, description});
}

async function deleteLocation(id){
  return apiPost('/api/locations/delete', {location_id: id});
}

async function connectLocations(a, b, status='open'){
  return apiPost('/api/locations/connect', {a, b, status});
}

async function disconnectLocations(a, b){
  return apiPost('/api/locations/disconnect', {a, b});
}

window.createLocation = createLocation;
window.deleteLocation = deleteLocation;
window.connectLocations = connectLocations;
window.disconnectLocations = disconnectLocations;
