bind = "unix:/run/klosa/gunicorn.sock"
workers = 3
timeout = 30
max_requests = 1000
max_requests_jitter = 100
accesslog = "-"
errorlog = "-"
limit_request_line = 4094