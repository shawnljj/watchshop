/* Station scanning.
 *
 * The design goal: a scan is ONE tap and never a page of typing. Three tiers,
 * best first, because the shop's devices will not all be the same:
 *
 *   1. BarcodeDetector in a live camera view - the scan button opens the camera,
 *      a detected tag navigates straight to the job. No typing at all.
 *   2. The phone's own camera app, pointed at the tag - the tag is a short URL,
 *      so the job page opens with no app involvement whatsoever. This is the
 *      fallback when the browser has no in-page scanner.
 *   3. The manual code box, always present, for a worn or torn tag.
 *
 * A camera that cannot start is a normal state, not an error: it is reported in
 * plain words and the other two paths stay available.
 */
(function () {
  var station = document.body.dataset.station || location.pathname.split('/')[2];
  var btn = document.getElementById('scanbtn');
  var input = document.getElementById('scaninput');
  var msg = document.getElementById('scanmsg');

  function say(text, tone) {
    if (!msg) return;
    msg.hidden = false;
    msg.textContent = text;
    msg.style.borderColor = tone === 'bad' ? 'var(--bad)' : 'var(--line)';
  }

  function open(code) {
    location.href = '/st/' + station + '/move/' + String(code).trim().toUpperCase();
  }

  // A scanned URL is /j/<CODE>; a scanned short code is <CODE> itself.
  function codeFrom(text) {
    var m = String(text).match(/\/j\/([A-Za-z0-9]{4,16})/);
    if (m) return m[1];
    var c = String(text).trim().toUpperCase();
    return /^[A-Z0-9]{4,16}$/.test(c) ? c : null;
  }

  function canScanLive() {
    return ('BarcodeDetector' in window) &&
           navigator.mediaDevices &&
           typeof navigator.mediaDevices.getUserMedia === 'function';
  }

  var scanning = false;

  function stop() {
    scanning = false;
    var v = document.getElementById('scanview');
    if (v) {
      if (v.srcObject) v.srcObject.getTracks().forEach(function (t) { t.stop(); });
      v.remove();
    }
  }

  async function startLive() {
    if (scanning) { stop(); return; }
    var video = document.createElement('video');
    video.id = 'scanview';
    video.setAttribute('playsinline', '');
    video.muted = true;
    video.style.cssText = 'width:100%;border-radius:20px;margin-top:10px;background:#000';
    btn.insertAdjacentElement('afterend', video);
    try {
      video.srcObject = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: 'environment' } }, audio: false
      });
      await video.play();
      scanning = true;
      say('Point the camera at the tag. It opens by itself.');
    } catch (e) {
      video.remove();
      say(e && e.name === 'NotAllowedError'
        ? 'Camera access was declined. Use your phone\u2019s Camera app on the tag instead, or type the code below.'
        : 'The camera did not start here. Use your phone\u2019s Camera app on the tag, or type the code below.', 'bad');
      return;
    }

    var detector;
    try {
      detector = new window.BarcodeDetector({ formats: ['qr_code'] });
    } catch (e) {
      say('This browser cannot read QR codes in the page. Use your phone\u2019s Camera app on the tag.', 'bad');
      return;
    }
    // Some detectors need a moment before the first frame is usable.
    var last = 0;
    async function tick() {
      if (!scanning) return;
      var now = Date.now();
      if (now - last > 250) {
        last = now;
        try {
          var found = await detector.detect(video);
          for (var i = 0; i < found.length; i++) {
            var code = codeFrom(found[i].rawValue);
            if (code) { stop(); open(code); return; }
          }
        } catch (e) { /* a frame can fail while the camera settles */ }
      }
      requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
  }

  if (btn) {
    btn.addEventListener('click', function (ev) {
      if (canScanLive()) {
        ev.preventDefault();
        startLive();
      } else {
        ev.preventDefault();
        say('This browser has no in-page scanner, so use your phone\u2019s Camera app on the tag: it opens the job directly. The code box below works too.');
      }
    });
  }

  if (input) {
    input.addEventListener('change', function () {
      say(canScanLive()
        ? 'Reading the photo is not supported here. Use the live scanner or the Camera app.'
        : 'Use the Camera app on the tag, or type the code below.');
      input.value = '';
    });
  }

  // Keep the manual box in step with the device keyboard: phones like to
  // capitalise, and a job code is upper case by design.
  var manual = document.querySelector('#manualform input[name=code]');
  if (manual) {
    manual.addEventListener('input', function () {
      var at = manual.selectionStart;
      manual.value = manual.value.toUpperCase();
      try { manual.setSelectionRange(at, at); } catch (e) {}
    });
  }

  window.addEventListener('pagehide', stop);
})();
