import logging
import queue
from logging.handlers import QueueHandler, QueueListener

class QueueLoggerDict:
    def __init__(self, logger: logging.Logger, listener: QueueListener):
        self.logger = logger
        self.listener = listener

def get_queue_logger(
    max_queue_size: int = 1000,
    logger_name: str = "request-logger",
    log_level: int = logging.INFO,    
) -> QueueLoggerDict:
    """get QueueHandler logger with its QueueListener<br/>
    
    you should trigger `listener.start()` for queue logger working properly
    
    Args:
        max_queue_size (int, optional): max queue size to apply on queue handlers queue. Defaults to 1000.
        logger_name (str, optional): logger name. Defaults to "request-logger".
        log_level (int, optional): default logging level. Defaults to logging.INFO.
    Returns:
        class: QueueLoggerDict("logger": logger, "listener": listener)
    """
    log_queue = queue.Queue(maxsize=max_queue_size)
    queue_handler = QueueHandler(log_queue)
    
    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(log_level)
    formatter = logging.Formatter("%(message)s")
    stream_handler.setFormatter(formatter)
    
    listener = QueueListener(log_queue, stream_handler)
    logger = logging.getLogger(logger_name)
    logger.setLevel(log_level)
    logger.handlers.clear()
    logger.addHandler(queue_handler)
    logger.propagate = False
    
    return QueueLoggerDict(logger=logger, listener=listener)

def get_logger(logger_name: str = "request-logger", log_level: int = logging.INFO) -> logging.Logger:    
    logger = logging.getLogger(logger_name)
    logger.setLevel(log_level)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(stream_handler)
    return logger