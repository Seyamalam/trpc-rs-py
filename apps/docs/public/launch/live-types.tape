# live-types demo: `trpc-py dev` regenerating app-router.d.ts on save.
#
# Self-contained: builds a starter project in a temp dir, starts the
# watcher, edits server.py, and shows the new procedure appearing in the
# generated declarations. Produce the GIF with:
#
#     vhs docs/launch/live-types.tape

Output live-types.gif

Set Shell bash
Set FontSize 14
Set Width 1200
Set Height 700
Set TypingSpeed 50ms
Set Theme "Catppuccin Mocha"

# Scaffold a starter.
Type "cd $(mktemp -d) && trpc-py init demo && cd demo"
Sleep 500ms
Enter
Sleep 2s

# Start the dev watcher in the background, log to a file.
Type "trpc-py dev --app server:router --out web/app-router.d.ts > dev.log 2>&1 &"
Sleep 300ms
Enter
Sleep 3s

# Show the generated router types exist.
Type "grep Procedure web/app-router.d.ts | head -5"
Enter
Sleep 2s

# Watch the d.ts for the new procedure while we append it to server.py.
Type "tail -f web/app-router.d.ts &"
Enter
Sleep 1s

Type 'cat >> server.py <<EOF'
Enter
Type '@router.mutation'
Enter
Type 'def delete_user(user_id: int) -> bool:'
Enter
Type '    return True'
Enter
Type 'EOF'
Enter

# The debounced regen fires ~1s after the write.
Sleep 3s
Type 'grep -A2 "delete_user" web/app-router.d.ts'
Enter
Sleep 4s
