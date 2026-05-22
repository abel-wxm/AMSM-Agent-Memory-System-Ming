from memoir.provider import AMSMProvider

def register(ctx):
    ctx.register_memory_provider(AMSMProvider())
