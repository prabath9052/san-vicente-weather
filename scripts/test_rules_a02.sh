#!/usr/bin/env bash
# A-02 post-publish check (no passwords needed). Write protection of usernames/prabath9052 can't be tested without a login;
# it is enforced by the rules text (check in console Rules Playground if wanted).
K=AIzaSyD2kxZAxQw2Ux46PVBSI6HsuD0-Pc1ko0I; FS="https://firestore.googleapis.com/v1/projects/san-vicente-weather/databases/(default)/documents"
c=$(curl -s -o /dev/null -w '%{http_code}' "$FS/usernames?key=$K"); echo "unauth list: HTTP $c (expect 403)"
c=$(curl -s -o /dev/null -w '%{http_code}' "$FS/usernames/prabath9052?key=$K"); echo "unauth get prabath9052: HTTP $c (expect 200)"
c=$(curl -s -o /dev/null -w '%{http_code}' -X DELETE "$FS/usernames/prabath9052?key=$K"); echo "unauth delete prabath9052: HTTP $c (expect 403)"
c=$(curl -s -o /dev/null -w '%{http_code}' "$FS/alertNominees?key=$K"); echo "unauth list alertNominees: HTTP $c (expect 403)"
