#property strict
#property version   "3.00"
#property description "FXPilot AI Ideas Trader — сигналы сайта, риск, BE, трейлинг"
#property copyright "FXPilot"

/*
  MT4: Сервис → Настройки → Советники
  ☑ Разрешить WebRequest для следующих URL:
     https://fxpilot.ru
  На график: Советники → AI_Ideas_Trader
*/

input string InpApiBaseUrl            = "https://fxpilot.ru";
input int    InpMagicNumber           = 26042026;
input int    InpRefreshSeconds        = 30;
input int    InpMarkupRefreshSeconds  = 60;

input double InpLots                  = 0.01;
input bool   InpUseRiskPercent        = true;
input double InpRiskPercent           = 0.50;
input double InpMaxLots               = 0.20;

input int    InpMaxSpreadPoints       = 35;
input int    InpSlippage              = 10;
input bool   InpOneTradePerSymbol     = true;
input int    InpReentryCooldownSec    = 180;

input bool   InpAllowBuy              = true;
input bool   InpAllowSell             = true;
input bool   InpUseConfidenceFilter   = true;
input int    InpMinConfidence         = 55;
input double InpMinRR                 = 1.3;

input bool   InpUseEntryZone          = true;
input bool   InpUseBufferedSL         = true;
input bool   InpSkipIfTpTooClose      = true;

input bool   InpUseTrailingStop       = true;
input int    InpTrailStartPoints      = 180;
input int    InpTrailDistancePoints   = 220;
input int    InpTrailStepPoints       = 20;
input bool   InpUseBreakEven          = true;
input int    InpBETriggerPoints       = 160;
input int    InpBELockPoints          = 20;

input bool   InpFridayClose           = false;
input int    InpFridayCloseHour       = 21;

input string InpTradeComment          = "FXPilot";

#define EA_PREFIX     "FXPILOT_EA_"
#define MARKUP_PREFIX "AI_MARKUP_"
#define BTN_POLL      "FXPILOT_EA_BTN_POLL"

string   g_apiBase;
datetime g_lastPoll = 0;
datetime g_lastMarkup = 0;
datetime g_lastCloseAt = 0;
string   g_lastHttp = "—";
string   g_lastIdeaId = "";
string   g_lastAction = "—";
string   g_lastReason = "ожидание сигнала";
int      g_found = 0;
int      g_httpCode = 0;
double   g_lastEntry = 0, g_lastSL = 0, g_lastTP = 0;
int      g_lastConfidence = 0;

string ToUpper(string s) { StringToUpper(s); return s; }
string ToLower(string s) { StringToLower(s); return s; }
string TrimStr(string s) { StringTrimLeft(s); StringTrimRight(s); return s; }

string JoinUrl(string base, string path)
{
   base = TrimStr(base);
   int n = StringLen(base);
   if(n > 0 && StringGetCharacter(base, n - 1) == '/')
      base = StringSubstr(base, 0, n - 1);
   if(StringFind(path, "/") != 0) path = "/" + path;
   return base + path;
}

int DigitsFor(string symbol)
{
   int d = (int)MarketInfo(symbol, MODE_DIGITS);
   if(d <= 0) d = Digits;
   return d;
}

double PointFor(string symbol)
{
   double p = MarketInfo(symbol, MODE_POINT);
   if(p <= 0) p = Point;
   return p;
}

bool SymbolMatches(string brokerSymbol, string apiSymbol)
{
   if(StringLen(apiSymbol) == 0) return false;
   string b = ToUpper(brokerSymbol);
   string a = ToUpper(apiSymbol);
   StringReplace(a, "/", "");
   StringReplace(a, "-", "");
   StringReplace(a, " ", "");
   if(b == a) return true;
   if(StringFind(b, a) == 0) return true;
   if(StringFind(a, b) == 0) return true;
   return false;
}

int StopLevelPoints(string symbol)
{
   int stopLevel = (int)MarketInfo(symbol, MODE_STOPLEVEL);
   int freeze = (int)MarketInfo(symbol, MODE_FREEZELEVEL);
   int minPts = MathMax(stopLevel, freeze);
   if(minPts < 0) minPts = 0;
   return minPts;
}

double NormalizePrice(string symbol, double price)
{
   return NormalizeDouble(price, DigitsFor(symbol));
}

bool HttpGet(string url, string &response)
{
   char post[];
   char result[];
   string headers;
   ResetLastError();
   int code = WebRequest("GET", url, "", 12000, post, result, headers);
   g_httpCode = code;
   if(code == -1)
   {
      int err = GetLastError();
      g_lastHttp = "WebRequest err " + IntegerToString(err);
      Print("FXPilot EA: ", g_lastHttp, " URL=", url);
      if(err == 4060 || err == 4014)
         Print("FXPilot EA: добавь https://fxpilot.ru в список разрешённых URL WebRequest");
      response = "";
      return false;
   }
   response = CharArrayToString(result, 0, WHOLE_ARRAY, CP_UTF8);
   if(code < 200 || code >= 300)
   {
      g_lastHttp = "HTTP " + IntegerToString(code);
      Print("FXPilot EA: ", g_lastHttp);
      return false;
   }
   g_lastHttp = "HTTP " + IntegerToString(code);
   return true;
}

int FindArrayEnd(string text, int arrayStart)
{
   int depth = 0;
   bool inString = false;
   int n = StringLen(text);
   for(int i = arrayStart; i < n; i++)
   {
      int c = StringGetCharacter(text, i);
      int prev = (i > 0) ? StringGetCharacter(text, i - 1) : 0;
      if(c == '"' && prev != '\\') inString = !inString;
      if(inString) continue;
      if(c == '[') depth++;
      if(c == ']')
      {
         depth--;
         if(depth == 0) return i;
      }
   }
   return -1;
}

int FindObjectEnd(string text, int objStart)
{
   int depth = 0;
   bool inString = false;
   int n = StringLen(text);
   for(int i = objStart; i < n; i++)
   {
      int c = StringGetCharacter(text, i);
      int prev = (i > 0) ? StringGetCharacter(text, i - 1) : 0;
      if(c == '"' && prev != '\\') inString = !inString;
      if(inString) continue;
      if(c == '{') depth++;
      if(c == '}')
      {
         depth--;
         if(depth == 0) return i;
      }
   }
   return -1;
}

int JsonValueStart(string obj, string key)
{
   string marker = "\"" + key + "\"";
   int k = StringFind(obj, marker);
   if(k < 0) return -1;
   int colon = StringFind(obj, ":", k + StringLen(marker));
   if(colon < 0) return -1;
   int start = colon + 1;
   int n = StringLen(obj);
   while(start < n)
   {
      int c = StringGetCharacter(obj, start);
      if(c == ' ' || c == '\n' || c == '\r' || c == '\t') start++;
      else break;
   }
   return start;
}

bool JsonHasKey(string obj, string key)
{
   return (StringFind(obj, "\"" + key + "\"") >= 0);
}

string JsonGetString(string obj, string key)
{
   int start = JsonValueStart(obj, key);
   if(start < 0) return "";
   if(StringGetCharacter(obj, start) != '"') return "";
   int q1 = start;
   int q2 = -1;
   int n = StringLen(obj);
   for(int i = q1 + 1; i < n; i++)
   {
      int c = StringGetCharacter(obj, i);
      int prev = StringGetCharacter(obj, i - 1);
      if(c == '"' && prev != '\\') { q2 = i; break; }
   }
   if(q2 < 0) return "";
   return StringSubstr(obj, q1 + 1, q2 - q1 - 1);
}

string JsonGetStringAny(string obj, string k1, string k2, string k3)
{
   string v = JsonGetString(obj, k1);
   if(StringLen(v) > 0) return v;
   v = JsonGetString(obj, k2);
   if(StringLen(v) > 0) return v;
   if(StringLen(k3) > 0) return JsonGetString(obj, k3);
   return "";
}

double JsonGetNumber(string obj, string key)
{
   int start = JsonValueStart(obj, key);
   if(start < 0) return 0.0;
   int c0 = StringGetCharacter(obj, start);
   if(c0 == 'n' || c0 == 'N' || c0 == '"')
   {
      if(c0 == '"')
      {
         string s = JsonGetString(obj, key);
         return StrToDouble(s);
      }
      return 0.0;
   }
   int end = start;
   int n = StringLen(obj);
   while(end < n)
   {
      int c = StringGetCharacter(obj, end);
      if((c >= '0' && c <= '9') || c == '.' || c == '-' || c == '+') end++;
      else break;
   }
   if(end <= start) return 0.0;
   return StrToDouble(StringSubstr(obj, start, end - start));
}

bool JsonGetBool(string obj, string key)
{
   int start = JsonValueStart(obj, key);
   if(start < 0) return false;
   string rem = ToLower(StringSubstr(obj, start, 5));
   return (StringFind(rem, "true") == 0);
}

int CountOpenTrades(string symbol, int magic)
{
   int count = 0;
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES)) continue;
      if(OrderSymbol() != symbol) continue;
      if(OrderMagicNumber() != magic) continue;
      int t = OrderType();
      if(t == OP_BUY || t == OP_SELL) count++;
   }
   return count;
}

double VolumeStepLots(string symbol, double lots)
{
   double minLot = MarketInfo(symbol, MODE_MINLOT);
   double maxLot = MarketInfo(symbol, MODE_MAXLOT);
   double step = MarketInfo(symbol, MODE_LOTSTEP);
   if(minLot <= 0) minLot = 0.01;
   if(maxLot <= 0) maxLot = 100;
   if(step <= 0) step = 0.01;
   if(lots < minLot) lots = minLot;
   if(lots > maxLot) lots = maxLot;
   if(lots > InpMaxLots) lots = InpMaxLots;
   lots = MathFloor(lots / step + 1e-8) * step;
   if(lots < minLot) lots = minLot;
   return NormalizeDouble(lots, 2);
}

double LotsByRisk(string symbol, double entry, double sl)
{
   if(!InpUseRiskPercent || InpRiskPercent <= 0) return VolumeStepLots(symbol, InpLots);
   double dist = MathAbs(entry - sl);
   if(dist <= 0) return VolumeStepLots(symbol, InpLots);
   double tickSize = MarketInfo(symbol, MODE_TICKSIZE);
   double tickValue = MarketInfo(symbol, MODE_TICKVALUE);
   if(tickSize <= 0 || tickValue <= 0) return VolumeStepLots(symbol, InpLots);
   double riskMoney = AccountEquity() * InpRiskPercent / 100.0;
   double ticks = dist / tickSize;
   if(ticks <= 0) return VolumeStepLots(symbol, InpLots);
   double lots = riskMoney / (ticks * tickValue);
   return VolumeStepLots(symbol, lots);
}

bool IsSpreadOk(string symbol)
{
   double point = PointFor(symbol);
   if(point <= 0) return false;
   double spread = (MarketInfo(symbol, MODE_ASK) - MarketInfo(symbol, MODE_BID)) / point;
   return (spread <= InpMaxSpreadPoints);
}

bool ValidLevels(string action, double entry, double sl, double tp)
{
   if(entry <= 0 || sl <= 0 || tp <= 0) return false;
   if(action == "BUY") return (sl < entry && tp > entry);
   if(action == "SELL") return (tp < entry && sl > entry);
   return false;
}

double RewardRisk(string action, double entry, double sl, double tp)
{
   double risk = MathAbs(entry - sl);
   if(risk <= 0) return 0;
   return MathAbs(tp - entry) / risk;
}

bool PriceInEntryZone(string action, double fromPrice, double toPrice)
{
   if(fromPrice <= 0 || toPrice <= 0) return true;
   double lo = MathMin(fromPrice, toPrice);
   double hi = MathMax(fromPrice, toPrice);
   double pad = 3 * Point;
   if(action == "BUY") return (Ask <= hi + pad);
   if(action == "SELL") return (Bid >= lo - pad);
   return false;
}

void AdjustStops(string symbol, int type, double price, double &sl, double &tp)
{
   int minPts = StopLevelPoints(symbol);
   double point = PointFor(symbol);
   double minDist = minPts * point;
   if(type == OP_BUY)
   {
      if(sl > 0 && price - sl < minDist) sl = NormalizePrice(symbol, price - minDist);
      if(tp > 0 && tp - price < minDist) tp = NormalizePrice(symbol, price + minDist);
   }
   else
   {
      if(sl > 0 && sl - price < minDist) sl = NormalizePrice(symbol, price + minDist);
      if(tp > 0 && price - tp < minDist) tp = NormalizePrice(symbol, price - minDist);
   }
}

void ClearMarkup()
{
   for(int i = ObjectsTotal() - 1; i >= 0; i--)
   {
      string name = ObjectName(i);
      if(StringFind(name, MARKUP_PREFIX) == 0) ObjectDelete(0, name);
   }
}

void DrawHLine(string name, double price, color clr, int width)
{
   if(price <= 0) return;
   if(ObjectFind(0, name) < 0)
      ObjectCreate(0, name, OBJ_HLINE, 0, 0, price);
   ObjectSetDouble(0, name, OBJPROP_PRICE1, price);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, width);
   ObjectSetInteger(0, name, OBJPROP_STYLE, STYLE_SOLID);
   ObjectSetInteger(0, name, OBJPROP_BACK, true);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
}

void DrawZone(string json)
{
   int p = StringFind(json, "\"entry_zone\"");
   if(p < 0) return;
   string chunk = StringSubstr(json, p, 420);
   double fromPrice = JsonGetNumber(chunk, "from_price");
   if(fromPrice <= 0) fromPrice = JsonGetNumber(chunk, "from");
   double toPrice = JsonGetNumber(chunk, "to_price");
   if(toPrice <= 0) toPrice = JsonGetNumber(chunk, "to");
   if(fromPrice <= 0 || toPrice <= 0) return;
   datetime t1 = Time[MathMin(Bars - 1, 160)];
   datetime t2 = Time[0] + Period() * 60 * 8;
   string name = MARKUP_PREFIX + "ENTRY_ZONE";
   if(ObjectFind(0, name) < 0)
      ObjectCreate(0, name, OBJ_RECTANGLE, 0, t1, fromPrice, t2, toPrice);
   ObjectSet(name, OBJPROP_TIME1, t1);
   ObjectSet(name, OBJPROP_TIME2, t2);
   ObjectSet(name, OBJPROP_PRICE1, fromPrice);
   ObjectSet(name, OBJPROP_PRICE2, toPrice);
   ObjectSet(name, OBJPROP_COLOR, C'48,78,140');
   ObjectSet(name, OBJPROP_BACK, true);
   ObjectSet(name, OBJPROP_WIDTH, 1);
   ObjectSet(name, OBJPROP_SELECTABLE, false);
}

void DrawMarkup()
{
   if(TimeCurrent() - g_lastMarkup < InpMarkupRefreshSeconds) return;
   g_lastMarkup = TimeCurrent();
   string url = JoinUrl(g_apiBase, "/api/mt4/markup/" + Symbol() + "?tf=M15");
   string response = "";
   if(!HttpGet(url, response))
   {
      Print("FXPilot EA: markup недоступен");
      return;
   }
   ClearMarkup();
   int levelsPos = StringFind(response, "\"levels\"");
   if(levelsPos >= 0)
   {
      int arrayStart = StringFind(response, "[", levelsPos);
      int arrayEnd = FindArrayEnd(response, arrayStart);
      if(arrayStart >= 0 && arrayEnd > arrayStart)
      {
         string arr = StringSubstr(response, arrayStart + 1, arrayEnd - arrayStart - 1);
         int pos = 0;
         int tpIndex = 0;
         while(true)
         {
            int objStart = StringFind(arr, "{", pos);
            if(objStart < 0) break;
            int objEnd = FindObjectEnd(arr, objStart);
            if(objEnd < 0) break;
            string obj = StringSubstr(arr, objStart, objEnd - objStart + 1);
            string typ = ToLower(JsonGetString(obj, "type"));
            double price = JsonGetNumber(obj, "price");
            if(typ == "entry") DrawHLine(MARKUP_PREFIX + "ENTRY", price, C'70,170,255', 2);
            else if(typ == "sl") DrawHLine(MARKUP_PREFIX + "SL", price, C'230,80,80', 2);
            else if(typ == "tp")
            {
               tpIndex++;
               DrawHLine(MARKUP_PREFIX + "TP" + IntegerToString(tpIndex), price, C'70,210,120', 2);
            }
            pos = objEnd + 1;
         }
      }
   }
   DrawZone(response);
   ChartRedraw();
}

void SetLabel(string name, string value, int x, int y, int size, color clr)
{
   if(ObjectFind(0, name) < 0) ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
   ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, size);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetString(0, name, OBJPROP_FONT, "Arial");
   ObjectSetString(0, name, OBJPROP_TEXT, value);
}

void CreatePanel()
{
   string bg = EA_PREFIX + "BG";
   if(ObjectFind(0, bg) < 0) ObjectCreate(0, bg, OBJ_RECTANGLE_LABEL, 0, 0, 0);
   ObjectSetInteger(0, bg, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, bg, OBJPROP_XDISTANCE, 12);
   ObjectSetInteger(0, bg, OBJPROP_YDISTANCE, 18);
   ObjectSetInteger(0, bg, OBJPROP_XSIZE, 360);
   ObjectSetInteger(0, bg, OBJPROP_YSIZE, 188);
   ObjectSetInteger(0, bg, OBJPROP_BGCOLOR, C'18,24,34');
   ObjectSetInteger(0, bg, OBJPROP_BORDER_COLOR, C'55,75,95');
   ObjectSetInteger(0, bg, OBJPROP_BACK, false);
   ObjectSetInteger(0, bg, OBJPROP_SELECTABLE, false);

   if(ObjectFind(0, BTN_POLL) < 0) ObjectCreate(0, BTN_POLL, OBJ_BUTTON, 0, 0, 0);
   ObjectSetInteger(0, BTN_POLL, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, BTN_POLL, OBJPROP_XDISTANCE, 250);
   ObjectSetInteger(0, BTN_POLL, OBJPROP_YDISTANCE, 166);
   ObjectSetInteger(0, BTN_POLL, OBJPROP_XSIZE, 108);
   ObjectSetInteger(0, BTN_POLL, OBJPROP_YSIZE, 26);
   ObjectSetInteger(0, BTN_POLL, OBJPROP_BGCOLOR, C'25,115,165');
   ObjectSetInteger(0, BTN_POLL, OBJPROP_COLOR, clrWhite);
   ObjectSetString(0, BTN_POLL, OBJPROP_TEXT, "ОБНОВИТЬ");
}

void UpdatePanel()
{
   CreatePanel();
   int openTrades = CountOpenTrades(Symbol(), InpMagicNumber);
   color statusClr = C'90,220,130';
   if(g_httpCode != 200 && g_httpCode != 0) statusClr = C'255,120,90';
   SetLabel(EA_PREFIX + "T", "FXPILOT  |  IDEAS TRADER v3.00", 24, 28, 11, C'70,210,255');
   SetLabel(EA_PREFIX + "S", Symbol() + "   magic " + IntegerToString(InpMagicNumber), 24, 50, 9, C'170,185,200');
   SetLabel(EA_PREFIX + "H", "Связь: " + g_lastHttp, 24, 70, 9, statusClr);
   SetLabel(EA_PREFIX + "I", "Сигнал: " + g_lastAction + "   conf " + IntegerToString(g_lastConfidence), 24, 90, 9, clrWhite);
   SetLabel(EA_PREFIX + "L",
            "E " + DoubleToString(g_lastEntry, Digits) +
            "  SL " + DoubleToString(g_lastSL, Digits) +
            "  TP " + DoubleToString(g_lastTP, Digits), 24, 110, 9, C'200,210,220');
   SetLabel(EA_PREFIX + "O", "Сделок: " + IntegerToString(openTrades) + "   найдено: " + IntegerToString(g_found), 24, 130, 9, C'170,185,200');
   SetLabel(EA_PREFIX + "R", "Статус: " + g_lastReason, 24, 148, 9, C'255,200,90');
   ChartRedraw();
}

void DestroyUi()
{
   string names[] = {
      EA_PREFIX + "BG", EA_PREFIX + "T", EA_PREFIX + "S", EA_PREFIX + "H",
      EA_PREFIX + "I", EA_PREFIX + "L", EA_PREFIX + "O", EA_PREFIX + "R", BTN_POLL
   };
   for(int i = 0; i < ArraySize(names); i++) ObjectDelete(0, names[i]);
   ClearMarkup();
   Comment("");
}

bool ModifyStop(int ticket, double newSL, double newTP)
{
   if(!OrderSelect(ticket, SELECT_BY_TICKET)) return false;
   RefreshRates();
   double sl = NormalizePrice(OrderSymbol(), newSL);
   double tp = NormalizePrice(OrderSymbol(), newTP);
   if(MathAbs(sl - OrderStopLoss()) < PointFor(OrderSymbol()) && MathAbs(tp - OrderTakeProfit()) < PointFor(OrderSymbol()))
      return true;
   bool ok = OrderModify(ticket, OrderOpenPrice(), sl, tp, 0, clrNONE);
   if(!ok) Print("FXPilot EA: OrderModify failed ", GetLastError(), " ticket=", ticket);
   return ok;
}

void ManageOpenTrades()
{
   string symbol = Symbol();
   double point = PointFor(symbol);
   if(point <= 0) return;
   int minPts = StopLevelPoints(symbol);

   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES)) continue;
      if(OrderSymbol() != symbol) continue;
      if(OrderMagicNumber() != InpMagicNumber) continue;
      int type = OrderType();
      if(type != OP_BUY && type != OP_SELL) continue;

      RefreshRates();
      double bid = Bid, ask = Ask;
      double sl = OrderStopLoss();
      double tp = OrderTakeProfit();
      double openPx = OrderOpenPrice();
      int ticket = OrderTicket();
      double profitPts = (type == OP_BUY) ? (bid - openPx) / point : (openPx - ask) / point;

      if(InpUseBreakEven && InpBETriggerPoints > 0 && profitPts >= InpBETriggerPoints)
      {
         double be = (type == OP_BUY)
                     ? openPx + InpBELockPoints * point
                     : openPx - InpBELockPoints * point;
         be = NormalizePrice(symbol, be);
         if(type == OP_BUY && (sl <= 0 || sl < be - point))
            ModifyStop(ticket, be, tp);
         if(type == OP_SELL && (sl <= 0 || sl > be + point))
            ModifyStop(ticket, be, tp);
         if(!OrderSelect(ticket, SELECT_BY_TICKET)) continue;
         sl = OrderStopLoss();
      }

      if(!InpUseTrailingStop || InpTrailDistancePoints <= 0) continue;
      if(profitPts < InpTrailStartPoints) continue;

      double trail = InpTrailDistancePoints * point;
      double newSL = sl;
      if(type == OP_BUY)
      {
         newSL = NormalizePrice(symbol, bid - trail);
         if(newSL <= sl + InpTrailStepPoints * point) continue;
         if(bid - newSL < minPts * point) continue;
      }
      else
      {
         newSL = NormalizePrice(symbol, ask + trail);
         if(sl > 0 && newSL >= sl - InpTrailStepPoints * point) continue;
         if(newSL - ask < minPts * point) continue;
      }
      ModifyStop(ticket, newSL, tp);
   }
}

void CloseAllMagic()
{
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES)) continue;
      if(OrderSymbol() != Symbol()) continue;
      if(OrderMagicNumber() != InpMagicNumber) continue;
      int type = OrderType();
      if(type != OP_BUY && type != OP_SELL) continue;
      RefreshRates();
      double px = (type == OP_BUY) ? Bid : Ask;
      if(!OrderClose(OrderTicket(), OrderLots(), px, InpSlippage, clrOrange))
         Print("FXPilot EA: close failed ", GetLastError());
   }
}

bool FridayCloseNow()
{
   if(!InpFridayClose) return false;
   if(DayOfWeek() != 5) return false;
   return (TimeHour(TimeCurrent()) >= InpFridayCloseHour);
}

string BuildComment(string ideaId)
{
   string cmt = InpTradeComment;
   if(StringLen(ideaId) > 0) cmt = cmt + " " + ideaId;
   if(StringLen(cmt) > 31) cmt = StringSubstr(cmt, 0, 31);
   return cmt;
}

bool ExtractChartSignal(string json,
                        string &ideaId, string &action, double &entry, double &sl, double &tp,
                        double &zoneFrom, double &zoneTo, double &slBuffered,
                        int &confidence, string &skipReason)
{
   int signalsPos = StringFind(json, "\"signals\"");
   if(signalsPos < 0) signalsPos = StringFind(json, "\"ideas\"");
   if(signalsPos < 0) return false;
   int arrayStart = StringFind(json, "[", signalsPos);
   int arrayEnd = FindArrayEnd(json, arrayStart);
   if(arrayStart < 0 || arrayEnd <= arrayStart) return false;
   string arr = StringSubstr(json, arrayStart + 1, arrayEnd - arrayStart - 1);
   g_found = 0;
   int pos = 0;
   while(true)
   {
      int objStart = StringFind(arr, "{", pos);
      if(objStart < 0) break;
      int objEnd = FindObjectEnd(arr, objStart);
      if(objEnd < 0) break;
      string obj = StringSubstr(arr, objStart, objEnd - objStart + 1);
      g_found++;
      string apiSymbol = JsonGetStringAny(obj, "symbol", "pair", "instrument");
      if(SymbolMatches(Symbol(), apiSymbol))
      {
         ideaId = JsonGetStringAny(obj, "id", "signal_id", "idea_id");
         action = ToUpper(JsonGetStringAny(obj, "action", "signal", "side"));
         entry = JsonGetNumber(obj, "entry");
         if(entry <= 0) entry = JsonGetNumber(obj, "entry_price");
         sl = JsonGetNumber(obj, "sl");
         if(sl <= 0) sl = JsonGetNumber(obj, "stop_loss");
         slBuffered = JsonGetNumber(obj, "sl_buffered");
         if(slBuffered <= 0) slBuffered = JsonGetNumber(obj, "buffered_sl");
         tp = JsonGetNumber(obj, "tp");
         if(tp <= 0) tp = JsonGetNumber(obj, "take_profit");
         zoneFrom = JsonGetNumber(obj, "entry_zone_from");
         zoneTo = JsonGetNumber(obj, "entry_zone_to");
         if(zoneFrom <= 0 || zoneTo <= 0)
         {
            int zp = StringFind(obj, "\"entry_zone\"");
            if(zp >= 0)
            {
               string zc = StringSubstr(obj, zp, 280);
               zoneFrom = JsonGetNumber(zc, "from_price");
               zoneTo = JsonGetNumber(zc, "to_price");
            }
         }
         confidence = (int)JsonGetNumber(obj, "confidence");
         skipReason = JsonGetStringAny(obj, "skip_reason", "reason", "reason_ru");
         bool permission = true;
         if(JsonHasKey(obj, "trade_permission")) permission = JsonGetBool(obj, "trade_permission");
         else if(JsonHasKey(obj, "advisor_allowed")) permission = JsonGetBool(obj, "advisor_allowed");
         if(!permission)
         {
            g_lastReason = "нет разрешения";
            return false;
         }
         return true;
      }
      pos = objEnd + 1;
   }
   return false;
}

void OpenFromSignal(string ideaId, string action, double entry, double sl, double tp)
{
   RefreshRates();
   int type = (action == "BUY") ? OP_BUY : OP_SELL;
   double price = (type == OP_BUY) ? Ask : Bid;
   AdjustStops(Symbol(), type, price, sl, tp);
   sl = NormalizePrice(Symbol(), sl);
   tp = NormalizePrice(Symbol(), tp);
   double lots = LotsByRisk(Symbol(), price, sl);
   string cmt = BuildComment(ideaId);
   int ticket = OrderSend(Symbol(), type, lots, price, InpSlippage, sl, tp, cmt, InpMagicNumber, 0, (type == OP_BUY ? clrDodgerBlue : clrTomato));
   if(ticket < 0)
   {
      g_lastReason = "ошибка ордера " + IntegerToString(GetLastError());
      Print("FXPilot EA: ", g_lastReason);
      return;
   }
   g_lastCloseAt = 0;
   g_lastReason = "открыт #" + IntegerToString(ticket);
   Print("FXPilot EA: opened ", action, " ticket=", ticket, " lots=", lots, " sl=", sl, " tp=", tp);
}

void PollSignals()
{
   string url = JoinUrl(g_apiBase, "/api/mt4/signals");
   string response = "";
   if(!HttpGet(url, response))
   {
      g_lastReason = g_lastHttp;
      return;
   }

   string ideaId = "", action = "", skipReason = "";
   double entry = 0, sl = 0, tp = 0, zoneFrom = 0, zoneTo = 0, slBuffered = 0;
   int confidence = 0;
   if(!ExtractChartSignal(response, ideaId, action, entry, sl, tp, zoneFrom, zoneTo, slBuffered, confidence, skipReason))
   {
      if(g_found == 0) g_lastReason = "пустой список сигналов";
      else if(StringLen(g_lastReason) == 0 || g_lastReason == "ожидание сигнала")
         g_lastReason = "нет сигнала по " + Symbol();
      g_lastAction = "WAIT";
      return;
   }

   if(InpUseBufferedSL && slBuffered > 0) sl = slBuffered;
   entry = NormalizePrice(Symbol(), entry);
   sl = NormalizePrice(Symbol(), sl);
   tp = NormalizePrice(Symbol(), tp);
   g_lastIdeaId = ideaId;
   g_lastAction = action;
   g_lastEntry = entry;
   g_lastSL = sl;
   g_lastTP = tp;
   g_lastConfidence = confidence;

   if(action != "BUY" && action != "SELL")
   {
      g_lastReason = "WAIT";
      return;
   }
   if(InpSkipIfTpTooClose && skipReason == "tp_too_close")
   {
      g_lastReason = "TP слишком близко";
      return;
   }
   if(InpUseEntryZone && !PriceInEntryZone(action, zoneFrom, zoneTo))
   {
      g_lastReason = "цена вне зоны входа";
      return;
   }
   if(InpUseConfidenceFilter && confidence > 0 && confidence < InpMinConfidence)
   {
      g_lastReason = "confidence " + IntegerToString(confidence);
      return;
   }
   if(!ValidLevels(action, entry, sl, tp))
   {
      g_lastReason = "битые SL/TP";
      return;
   }
   if(RewardRisk(action, entry, sl, tp) < InpMinRR)
   {
      g_lastReason = "RR ниже " + DoubleToString(InpMinRR, 2);
      return;
   }
   if(!IsSpreadOk(Symbol()))
   {
      g_lastReason = "спред";
      return;
   }
   if(action == "BUY" && !InpAllowBuy) { g_lastReason = "BUY выключен"; return; }
   if(action == "SELL" && !InpAllowSell) { g_lastReason = "SELL выключен"; return; }
   if(InpOneTradePerSymbol && CountOpenTrades(Symbol(), InpMagicNumber) > 0)
   {
      g_lastReason = "уже в рынке";
      return;
   }
   if(g_lastCloseAt > 0 && TimeCurrent() - g_lastCloseAt < InpReentryCooldownSec)
   {
      g_lastReason = "пауза после закрытия";
      return;
   }

   OpenFromSignal(ideaId, action, entry, sl, tp);
}

void RememberCloses()
{
   static int lastOpen = -1;
   int nowOpen = CountOpenTrades(Symbol(), InpMagicNumber);
   if(lastOpen > 0 && nowOpen == 0) g_lastCloseAt = TimeCurrent();
   lastOpen = nowOpen;
}

int OnInit()
{
   g_apiBase = TrimStr(InpApiBaseUrl);
   if(StringLen(g_apiBase) == 0) g_apiBase = "https://fxpilot.ru";
   CreatePanel();
   EventSetTimer(1);
   g_lastReason = "старт";
   UpdatePanel();
   Print("FXPilot EA v3.00 init ", Symbol(), " magic=", InpMagicNumber, " api=", g_apiBase);
   PollSignals();
   DrawMarkup();
   UpdatePanel();
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   DestroyUi();
}

void OnTick()
{
   RememberCloses();
   if(FridayCloseNow())
   {
      if(CountOpenTrades(Symbol(), InpMagicNumber) > 0)
      {
         CloseAllMagic();
         g_lastReason = "пятничное закрытие";
      }
   }
   else
      ManageOpenTrades();
}

void OnTimer()
{
   if(TimeCurrent() - g_lastPoll >= InpRefreshSeconds)
   {
      g_lastPoll = TimeCurrent();
      PollSignals();
      DrawMarkup();
   }
   UpdatePanel();
}

void OnChartEvent(const int id, const long &lparam, const double &dparam, const string &sparam)
{
   if(id == CHARTEVENT_OBJECT_CLICK && sparam == BTN_POLL)
   {
      ObjectSetInteger(0, BTN_POLL, OBJPROP_STATE, false);
      g_lastPoll = 0;
      g_lastMarkup = 0;
      PollSignals();
      DrawMarkup();
      UpdatePanel();
   }
}
