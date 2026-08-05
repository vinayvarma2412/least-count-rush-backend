def log_to_file(msg):
    with open("/tmp/backend_debug.log", "a") as f:
        f.write(msg + "\n")
