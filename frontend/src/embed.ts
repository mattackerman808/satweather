// Opened inside another site: Tachbase's Weather page frames
// /live?embed=tachbase. The landing page introduces satweather to a
// first-time visitor; inside the host it's a detour, so there "/" and the
// brand go to /live instead. The flag is kept for the tab's session because
// in-app links drop the query string.
const KEY = "satweather.embed";

function detect(): string | null {
  const flag = new URLSearchParams(location.search).get("embed");
  try {
    if (flag) sessionStorage.setItem(KEY, flag);
    return flag || sessionStorage.getItem(KEY);
  } catch {
    return flag; // storage blocked: the URL is all we have
  }
}

export const embedded = detect();
export const home = embedded ? "/live" : "/";
