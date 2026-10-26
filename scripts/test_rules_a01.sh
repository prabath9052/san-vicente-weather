#!/usr/bin/env bash
# A-01 rules check. Usage: ADMIN_EMAIL=... ADMIN_PASSWORD=... bash scripts/test_rules_a01.sh  (admin creds optional; never printed)
K=AIzaSyD2kxZAxQw2Ux46PVBSI6HsuD0-Pc1ko0I; FS="https://firestore.googleapis.com/v1/projects/san-vicente-weather/databases/(default)/documents"
c=$(curl -s -o /dev/null -w '%{http_code}' "$FS/usernames?key=$K"); echo "unauth list: HTTP $c (expect 403)"
c=$(curl -s -o /dev/null -w '%{http_code}' "$FS/usernames/prabath9052?key=$K"); echo "unauth get one: HTTP $c (expect 200)"
if [ -n "$ADMIN_PASSWORD" ]; then
 T=$(curl -s "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=$K" -H 'Content-Type: application/json' \
   -d "{\"email\":\"$ADMIN_EMAIL\",\"password\":\"$ADMIN_PASSWORD\",\"returnSecureToken\":true}" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("idToken",""))')
 n=$(curl -s -H "Authorization: Bearer $T" "$FS/usernames?pageSize=50" | python3 -c 'import json,sys;d=json.load(sys.stdin);print(len(d.get("documents",[])) if "error" not in d else "ERR "+d["error"]["status"])')
 echo "admin list: $n docs (expect 6)"
fi
