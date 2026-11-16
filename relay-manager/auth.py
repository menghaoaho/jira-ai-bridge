"""Use the existing workbench account authority; never copy its passwords."""
import http.cookies
import json
import re
import urllib.error
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def admin_identity(cookie, url='http://127.0.0.1:18097/api/auth/me'):
    try:
        jar = http.cookies.SimpleCookie()
        jar.load(cookie or '')
        value = jar['jira_workbench_cloud_session'].value
        if not re.fullmatch(r'[A-Za-z0-9_-]{20,160}', value):
            return 401, None
    except (http.cookies.CookieError, KeyError):
        return 401, None
    req = urllib.request.Request(url, headers={'Cookie': 'jira_workbench_cloud_session=' + value})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(req, timeout=4) as response:
            data = json.loads(response.read(32768))
        user = data.get('user', {})
        if not isinstance(user, dict) or not user.get('username') or not user.get('enabled', True):
            return 401, None
        return (200, user) if user.get('role') == 'admin' else (403, None)
    except urllib.error.HTTPError as exc:
        return (401 if exc.code in (401, 403) else 503), None
    except (OSError, ValueError):
        return 503, None
