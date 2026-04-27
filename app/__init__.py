import warnings

warnings.filterwarnings("ignore", message="urllib3 v2 only supports OpenSSL")

import urllib3

urllib3.disable_warnings()
