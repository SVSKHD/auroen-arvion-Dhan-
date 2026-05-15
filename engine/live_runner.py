from brokers.dhan.market_feed import DhanMarketFeed
from core.logger import get_logger
from engine.anchor_engine import AnchorEngine

logger = get_logger("live_runner")


class LiveRunner:
    def __init__(self) -> None:
        self.feed = DhanMarketFeed()
        self.anchor_engine = AnchorEngine()

    def bootstrap(self, instruments: list[dict]) -> None:
        logger.info("Connecting websocket feed")
        self.feed.connect(instruments)

    def heartbeat(self) -> dict:
        return {
            "feed_connected": self.feed.ws is not None,
            "tracked_ticks": len(self.feed.latest_ticks),
        }
