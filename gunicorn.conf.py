workers = 1
threads = 4
timeout = 30
accesslog = "-"


def post_worker_init(worker):
    import threading
    import aioapp

    threading.Thread(target=aioapp.run_bot, daemon=True).start()
