#property strict

/*
  AI Ideas Trader (MT4)
  ---------------------------------
  MT4: Tools -> Options -> Expert Advisors -> Allow WebRequest for listed URL
  Add: https://your-domain.onrender.com
*/

input string ApiUrl = "https://your-domain.onrender.com/api/mt4/signals";
input double Lots = 0.01;
input int MagicNumber = 26042026;
input int RefreshSeconds = 60;
input int MarkupRefreshSeconds = 120;
input int MaxSpreadPoints = 30;
input int Slippage = 5;
input bool OneTradePerSymbol = true;
input bool UseConfidenceFilter = true;
input int MinConfidence = 60;
input bool AllowBuy = true;
input bool AllowSell = true;
input bool UseEntryZone = true;
input bool UseBufferedSL = true;
input bool SkipIfTpTooClose = true;
input string MarkupUrlTemplate = "https://your-domain.onrender.com/api/mt4/markup/{symbol}?tf=M15";

datetime g_lastPoll = 0;
datetime g_lastMarkupPoll = 0;

int OnInit()
{
   Print("AI_Ideas_Trader initialized. Symbol=", Symbol(), " Magic=", MagicNumber);
   return(INIT_SUCCEEDED);
}

void OnTick()
{
   if(TimeCurrent() - g_lastPoll < RefreshSeconds) return;
   g_lastPoll = TimeCurrent();
   PollSignalsAndTrade();
}

void PollSignalsAndTrade()
{
   string response = "";
   if(!HttpGet(ApiUrl, response)) return;

   string symbol = "", action = "", comment = "", skipReason = "";
   double entry = 0.0, sl = 0.0, tp = 0.0, entryZoneFrom = 0.0, entryZoneTo = 0.0, entryZoneMid = 0.0, slBuffered = 0.0;
   int confidence = 0;
   bool tradePermission = false;

   if(!ExtractSignalForCurrentSymbol(response, symbol, action, entry, sl, tp, confidence, tradePermission, comment, entryZoneFrom, entryZoneTo, entryZoneMid, slBuffered, skipReason))
   {
      Print("No matching tradable signal for ", Symbol());
      DrawMarkupForCurrentSymbol();
      return;
   }

   DrawMarkupForCurrentSymbol();

   if(SkipIfTpTooClose && skipReason == "tp_too_close")
   {
      Print("Skipped: TP too close according to backend safety");
      return;
   }

   if(UseEntryZone && !IsPriceAllowedByEntryZone(action, entryZoneFrom, entryZoneTo))
   {
      Print("Ждём цену в зоне Entry");
      return;
   }

   if(UseBufferedSL && slBuffered > 0) sl = slBuffered;

   Print("AI signal received: ", symbol, " ", action, " entry=", DoubleToString(entry, Digits), " sl=", DoubleToString(sl, Digits), " tp=", DoubleToString(tp, Digits));

   if(UseConfidenceFilter && confidence < MinConfidence)
   {
      Print("Skipped: confidence below filter");
      return;
   }

   if(!IsSpreadAllowed())
   {
      Print("Skipped: spread too high");
      return;
   }

   if(!IsValidLevels(action, entry, sl, tp))
   {
      Print("Skipped: invalid SL/TP for direction");
      return;
   }

   if(OneTradePerSymbol && HasOpenTradeForSymbol(Symbol(), MagicNumber))
   {
      Print("Skipped: duplicate trade exists for symbol + magic");
      return;
   }

   if(action == "BUY" && !AllowBuy)
   {
      Print("Skipped: BUY disabled by input");
      return;
   }
   if(action == "SELL" && !AllowSell)
   {
      Print("Skipped: SELL disabled by input");
      return;
   }

   int type = (action == "BUY") ? OP_BUY : OP_SELL;
   double price = (type == OP_BUY) ? Ask : Bid;
   int ticket = OrderSend(Symbol(), type, Lots, price, Slippage, sl, tp, "AI idea", MagicNumber, 0, clrDodgerBlue);
   if(ticket < 0)
   {
      Print("Order failed: error code ", GetLastError());
      return;
   }

   Print("Order opened. Ticket=", ticket);
}

bool HttpGet(string url, string &response)
{
   char postData[];
   char result[];
   string headers;
   int timeout = 10000;
   ResetLastError();
   int code = WebRequest("GET", url, "", timeout, postData, result, headers);
   if(code == -1)
   {
      Print("HTTP failed. Enable WebRequest for URL. Error=", GetLastError());
      return(false);
   }
   response = CharArrayToString(result);
   if(code < 200 || code >= 300)
   {
      Print("HTTP status not OK: ", code);
      return(false);
   }
   return(true);
}

bool ExtractSignalForCurrentSymbol(string json, string &symbol, string &action, double &entry, double &sl, double &tp, int &confidence, bool &tradePermission, string &comment, double &entryZoneFrom, double &entryZoneTo, double &entryZoneMid, double &slBuffered, string &skipReason)
{
   int signalsPos = StringFind(json, "\"signals\"");
   if(signalsPos < 0) return(false);

   int arrayStart = StringFind(json, "[", signalsPos);
   int arrayEnd = StringFind(json, "]", arrayStart);
   if(arrayStart < 0 || arrayEnd <= arrayStart) return(false);

   string arr = StringSubstr(json, arrayStart + 1, arrayEnd - arrayStart - 1);
   int pos = 0;
   while(true)
   {
      int objStart = StringFind(arr, "{", pos);
      if(objStart < 0) break;
      int objEnd = StringFind(arr, "}", objStart);
      if(objEnd < 0) break;

      string obj = StringSubstr(arr, objStart, objEnd - objStart + 1);
      string apiSymbol = JsonGetString(obj, "symbol");
      string apiAction = StringUpper(JsonGetString(obj, "action"));
      double apiEntry = JsonGetNumber(obj, "entry");
      double apiSl = JsonGetNumber(obj, "sl");
      double apiSlBuffered = JsonGetNumber(obj, "sl_buffered");
      double apiTp = JsonGetNumber(obj, "tp");
      double apiEntryZoneFrom = JsonGetNumber(obj, "entry_zone_from");
      double apiEntryZoneTo = JsonGetNumber(obj, "entry_zone_to");
      double apiEntryZoneMid = JsonGetNumber(obj, "entry_zone_mid");
      string apiSkipReason = JsonGetString(obj, "skip_reason");
      int apiConfidence = (int)JsonGetNumber(obj, "confidence");
      bool apiTradePermission = JsonGetBool(obj, "trade_permission");
      string apiDataStatus = StringLower(JsonGetString(obj, "data_status"));

      if(SymbolMatches(Symbol(), apiSymbol)
         && (apiAction == "BUY" || apiAction == "SELL")
         && apiEntry > 0 && apiSl > 0 && apiTp > 0
         && apiTradePermission
         && (apiDataStatus == "real" || apiDataStatus == "delayed"))
      {
         symbol = apiSymbol;
         action = apiAction;
         entry = NormalizeDouble(apiEntry, Digits);
         sl = NormalizeDouble(apiSl, Digits);
         slBuffered = NormalizeDouble(apiSlBuffered, Digits);
         tp = NormalizeDouble(apiTp, Digits);
         entryZoneFrom = NormalizeDouble(apiEntryZoneFrom, Digits);
         entryZoneTo = NormalizeDouble(apiEntryZoneTo, Digits);
         entryZoneMid = NormalizeDouble(apiEntryZoneMid, Digits);
         skipReason = apiSkipReason;
         confidence = apiConfidence;
         tradePermission = apiTradePermission;
         comment = JsonGetString(obj, "comment");
         return(true);
      }

      pos = objEnd + 1;
   }

   return(false);
}

bool IsPriceAllowedByEntryZone(string action, double fromPrice, double toPrice)
{
   if(fromPrice <= 0 || toPrice <= 0) return(true);
   double low = MathMin(fromPrice, toPrice);
   double high = MathMax(fromPrice, toPrice);
   if(action == "BUY") return(Ask <= high + (2 * Point));
   if(action == "SELL") return(Bid >= low - (2 * Point));
   return(false);
}

void DrawMarkupForCurrentSymbol()
{
   if(TimeCurrent() - g_lastMarkupPoll < MarkupRefreshSeconds) return;
   g_lastMarkupPoll = TimeCurrent();
   string url = MarkupUrlTemplate;
   StringReplace(url, "{symbol}", Symbol());
   string response = "";
   if(!HttpGet(url, response))
   {
      Print("Markup: unavailable");
      return;
   }
   ClearMarkupObjects();
   DrawLineFromLevel(response, "entry", clrDodgerBlue);
   DrawLineFromLevel(response, "sl", clrTomato);
   DrawLineFromLevel(response, "tp", clrLimeGreen);
   DrawEntryZone(response);
}

void ClearMarkupObjects()
{
   for(int i = ObjectsTotal() - 1; i >= 0; i--)
   {
      string name = ObjectName(i);
      if(StringFind(name, "AI_MARKUP_") == 0) ObjectDelete(name);
   }
}

void DrawLineFromLevel(string json, string levelType, color lineColor)
{
   string marker = "\"type\":\"" + levelType + "\"";
   int p = StringFind(json, marker);
   if(p < 0) return;
   int pricePos = StringFind(json, "\"price\":", p);
   if(pricePos < 0) return;
   double price = JsonGetNumber(StringSubstr(json, p, 200), "price");
   if(price <= 0) return;
   string objName = "AI_MARKUP_" + StringUpper(levelType);
   ObjectCreate(objName, OBJ_HLINE, 0, 0, price);
   ObjectSet(objName, OBJPROP_COLOR, lineColor);
}

void DrawEntryZone(string json)
{
   int p = StringFind(json, "\"entry_zone\"");
   if(p < 0) return;
   string chunk = StringSubstr(json, p, 300);
   double fromPrice = JsonGetNumber(chunk, "from_price");
   double toPrice = JsonGetNumber(chunk, "to_price");
   if(fromPrice <= 0 || toPrice <= 0) return;
   datetime t1 = Time[MathMin(Bars - 1, 120)];
   datetime t2 = Time[0];
   string name = "AI_MARKUP_ENTRY_ZONE";
   ObjectCreate(name, OBJ_RECTANGLE, 0, t1, fromPrice, t2, toPrice);
   ObjectSet(name, OBJPROP_COLOR, clrSlateBlue);
}

bool SymbolMatches(string brokerSymbol, string apiSymbol)
{
   if(StringLen(apiSymbol) == 0) return(false);
   string b = StringUpper(brokerSymbol);
   string a = StringUpper(apiSymbol);
   return(StringSubstr(b, 0, StringLen(a)) == a);
}

bool IsSpreadAllowed()
{
   double spreadPoints = (Ask - Bid) / Point;
   return(spreadPoints <= MaxSpreadPoints);
}

bool IsValidLevels(string action, double entry, double sl, double tp)
{
   if(entry <= 0 || sl <= 0 || tp <= 0) return(false);
   if(action == "BUY") return(sl < entry && tp > entry);
   if(action == "SELL") return(tp < entry && sl > entry);
   return(false);
}

bool HasOpenTradeForSymbol(string symbol, int magic)
{
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES)) continue;
      if(OrderSymbol() != symbol) continue;
      if(OrderMagicNumber() != magic) continue;
      int t = OrderType();
      if(t == OP_BUY || t == OP_SELL) return(true);
   }
   return(false);
}

string JsonGetString(string obj, string key)
{
   string marker = "\"" + key + "\"";
   int k = StringFind(obj, marker);
   if(k < 0) return("");
   int colon = StringFind(obj, ":", k);
   if(colon < 0) return("");
   int q1 = StringFind(obj, "\"", colon + 1);
   if(q1 < 0) return("");
   int q2 = StringFind(obj, "\"", q1 + 1);
   if(q2 < 0) return("");
   return(StringSubstr(obj, q1 + 1, q2 - q1 - 1));
}

double JsonGetNumber(string obj, string key)
{
   string marker = "\"" + key + "\"";
   int k = StringFind(obj, marker);
   if(k < 0) return(0.0);
   int colon = StringFind(obj, ":", k);
   if(colon < 0) return(0.0);
   int start = colon + 1;
   while(start < StringLen(obj) && (StringGetCharacter(obj, start) == ' ')) start++;
   int end = start;
   while(end < StringLen(obj))
   {
      int c = StringGetCharacter(obj, end);
      if((c >= '0' && c <= '9') || c == '.' || c == '-') end++;
      else break;
   }
   string num = StringSubstr(obj, start, end - start);
   return(StrToDouble(num));
}

bool JsonGetBool(string obj, string key)
{
   string marker = "\"" + key + "\"";
   int k = StringFind(obj, marker);
   if(k < 0) return(false);
   int colon = StringFind(obj, ":", k);
   if(colon < 0) return(false);
   int start = colon + 1;
   while(start < StringLen(obj) && (StringGetCharacter(obj, start) == ' ')) start++;
   string rem = StringSubstr(obj, start, 5);
  rem = StringLower(rem);
  return(StringFind(rem, "true") == 0);
}
