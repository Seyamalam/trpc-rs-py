# ai-chat demo: tracked SSE token streaming from a Python async generator.
#
# Records the server boot, a live token stream via curl -N, and a
# mid-stream cancel showing client-disconnect cancellation. Produce with:
#
#     vhs docs/launch/ai-chat.tape
#
# Run from the repo root (uses examples/ai-chat).

Output ai-chat.gif

Set Shell bash
Set FontSize 14
Set Width 1200
Set Height 700
Set TypingSpeed 50ms
Set Theme "Catppuccin Mocha"

Type "cd examples/ai-chat && uvicorn app:app --port 8765 &"
Enter
Sleep 3s

# Stream the chat subscription; each token is one tracked data frame.
Type 'curl -N "http://127.0.0.1:8765/trpc/chat?input=%7B%22prompt%22%3A%22build%20me%20a%20chat%20app%22%7D"'
Sleep 300ms
Enter

# Let ~4s of frames scroll by, then Ctrl-C: the generator is cancelled
# server-side — the differentiator vs. a buffered response.
Sleep 4s
Ctrl+C
Sleep 1s

# Same router answers REST for non-tRPC clients.
Type 'curl -s http://127.0.0.1:8765/api/models'
Enter
Sleep 3s

Type 'kill %1'
Enter
