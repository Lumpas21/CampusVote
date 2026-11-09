/* Small camera helper shared by the face setup and face check pages. */
window.FaceCam = (function () {
  var stream = null;

  function start(video) {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      return Promise.reject(new Error(
        "This browser cannot use the camera here. Open the site at http://127.0.0.1:5000 " +
        "(or https) in Chrome, Edge or Firefox."));
    }
    return navigator.mediaDevices
      .getUserMedia({ video: { facingMode: "user", width: { ideal: 960 }, height: { ideal: 720 } }, audio: false })
      .then(function (s) {
        stream = s;
        video.srcObject = s;
        return new Promise(function (resolve) {
          if (video.readyState >= 2 && video.videoWidth) return resolve();
          video.onloadeddata = function () { resolve(); };
        });
      })
      .then(function () { return video.play().catch(function () {}); })
      .catch(function (err) {
        var denied = err && (err.name === "NotAllowedError" || err.name === "SecurityError");
        var none = err && (err.name === "NotFoundError" || err.name === "DevicesNotFoundError");
        throw new Error(denied
          ? "Camera blocked. Click the camera icon in the address bar, choose Allow, then reload."
          : none ? "No camera found on this device."
          : (err && err.message) || "The camera could not be started.");
      });
  }

  function stop(video) {
    if (stream) { stream.getTracks().forEach(function (t) { t.stop(); }); stream = null; }
    if (video) video.srcObject = null;
  }

  /* Returns a JPEG data URL of the current frame (not mirrored). */
  function snap(video) {
    var w = video.videoWidth, h = video.videoHeight;
    if (!w || !h) return null;
    var scale = Math.min(1, 640 / Math.max(w, h));
    var c = document.createElement("canvas");
    c.width = Math.round(w * scale); c.height = Math.round(h * scale);
    c.getContext("2d").drawImage(video, 0, 0, c.width, c.height);
    return c.toDataURL("image/jpeg", 0.9);
  }

  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

  function postJSON(url, body, csrf) {
    return fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
      body: JSON.stringify(body)
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        return { status: res.status, data: data };
      });
    });
  }

  return { start: start, stop: stop, snap: snap, sleep: sleep, postJSON: postJSON };
})();
