# History and integrity protocol

Authenticated requests use `Authorization: Bearer <token>` and the configured
HTTP/HTTPS server. A cursor never grants access and must not be treated as a token.

`GET /history?target=<user-id>&limit=50` or `GET /history?gid=<group-id>&limit=50`
returns `messages`, `files`, `before` and `has_more`. Exactly one chat is required.
Limits are 1–100 total records per page, counting both messages and files.
Only members may view a group. A direct chat is always scoped to the signed-in
user and the selected other user.

To get older records, repeat the same chat and limit with `before=<URL-encoded
cursor>`. The cursor represents the oldest returned `(created, id, kind)` tuple.
Use it unchanged. `has_more=false` marks the end. Restart without a cursor to
return to the newest page. Newer navigation reuses earlier request cursors.
No entire chat is cached in a client; the displayed page is bounded.
Records use one chronology across both tables. Clients merge their two arrays
and sort ascending by `(created, id, kind)` for display.

`GET /file/info?id=<file-id>` returns public metadata for an authorized sender,
recipient or group member. It never reveals the physical stored filename.
Expired metadata remains readable, while `/download` returns HTTP 410.

Newly completed uploads store and return `sha256` (lowercase hex). `/state`,
`/history` and `/file/info` include the same digest. Older database records have
`sha256=null`; clients retain size-only checks for backward compatibility.
The digest validates content; it does not replace transport authentication/TLS.
