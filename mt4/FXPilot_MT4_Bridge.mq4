#property strict
#property version "2.13"
#property description "FXPilot: 4 symbols and 5 timeframes from one MT4 chart"

input string ServerUrl = "https://ai-forex-signal-platform.onrender.com/api/mt4/orderflow-batch";
input string ApiToken = "";
input string BrokerSymbolsCsv = "EURUSD,GBPUSD,USDJPY,XAUUSD";
input bool AutoDetectBrokerSymbols = true;
input int BarsToSend = 10;
input int SendEverySeconds = 60;
input bool SendOnStart = true;
input int HttpTimeoutMs = 10000;

#define SYMBOL_COUNT 4
#define TF_COUNT 5
#define BTN_NAME "FXPILOT_SEND_NOW"
#define PANEL_BG "FXPILOT_PANEL_BG"
#define PANEL_TITLE "FXPILOT_PANEL_TITLE"
#define PANEL_STATUS "FXPILOT_PANEL_STATUS"
#define PANEL_COUNTERS "FXPILOT_PANEL_COUNTERS"
#define PANEL_NEXT "FXPILOT_PANEL_NEXT"

string CanonicalSymbols[SYMBOL_COUNT] = {"EURUSD","GBPUSD","USDJPY","XAUUSD"};
string BrokerSymbols[SYMBOL_COUNT];
int Timeframes[TF_COUNT] = {PERIOD_M15,PERIOD_H1,PERIOD_H4,PERIOD_D1,PERIOD_W1};
long Samples[SYMBOL_COUNT], UpTicks[SYMBOL_COUNT], DownTicks[SYMBOL_COUNT], FlatTicks[SYMBOL_COUNT];
double LastBid[SYMBOL_COUNT], SpreadSum[SYMBOL_COUNT], SpreadMax[SYMBOL_COUNT];
datetime LastSendAt=0;
string LastStatus="Starting";
string LastErrorDetail="";
int OkPackets=0, FailedPackets=0;

string Trim(string value){ StringTrimLeft(value); StringTrimRight(value); return value; }
string TfName(int tf){ if(tf==PERIOD_M15)return "M15"; if(tf==PERIOD_H1)return "H1"; if(tf==PERIOD_H4)return "H4"; if(tf==PERIOD_D1)return "D1"; return "W1"; }
string Num(double value,int digits){ return DoubleToString(value,digits); }
string JsonEscape(string value){ StringReplace(value,"\\","\\\\"); StringReplace(value,"\"","\\\""); return value; }

string FindBrokerSymbol(string canonical,string preferredSuffix){
   string preferred=canonical+preferredSuffix;
   if(SymbolSelect(preferred,true) && MarketInfo(preferred,MODE_POINT)>0)return preferred;
   int total=SymbolsTotal(false);
   for(int i=0;i<total;i++){
      string candidate=SymbolName(i,false);
      if(StringFind(candidate,canonical,0)==0 && SymbolSelect(candidate,true) && MarketInfo(candidate,MODE_POINT)>0)return candidate;
   }
   return preferred;
}

void ResolveBrokerSymbols(string &parsed[],int count){
   string suffix="";
   string chartSymbol=Symbol();
   for(int i=0;i<SYMBOL_COUNT;i++){
      if(StringFind(chartSymbol,CanonicalSymbols[i],0)==0){
         suffix=StringSubstr(chartSymbol,StringLen(CanonicalSymbols[i]));
         break;
      }
   }
   for(int s=0;s<SYMBOL_COUNT;s++){
      if(AutoDetectBrokerSymbols)BrokerSymbols[s]=FindBrokerSymbol(CanonicalSymbols[s],suffix);
      else BrokerSymbols[s]=(count==SYMBOL_COUNT?Trim(parsed[s]):CanonicalSymbols[s]);
   }
}

void ResetStats(int i){ Samples[i]=0; UpTicks[i]=0; DownTicks[i]=0; FlatTicks[i]=0; SpreadSum[i]=0; SpreadMax[i]=0; }

void SampleSymbols(){
   for(int i=0;i<SYMBOL_COUNT;i++){
      string s=BrokerSymbols[i];
      double bid=MarketInfo(s,MODE_BID), ask=MarketInfo(s,MODE_ASK), point=MarketInfo(s,MODE_POINT);
      if(bid<=0 || ask<=0 || point<=0) continue;
      if(LastBid[i]>0){ if(bid>LastBid[i])UpTicks[i]++; else if(bid<LastBid[i])DownTicks[i]++; else FlatTicks[i]++; }
      LastBid[i]=bid; double spread=(ask-bid)/point; Samples[i]++; SpreadSum[i]+=spread; if(spread>SpreadMax[i])SpreadMax[i]=spread;
   }
}

string BuildCandles(string symbol,int tf){
   string out="["; int digits=(int)MarketInfo(symbol,MODE_DIGITS); int count=MathMin(BarsToSend,iBars(symbol,tf)); bool first=true;
   for(int shift=count-1;shift>=0;shift--){
      datetime t=iTime(symbol,tf,shift); if(t<=0)continue; if(!first)out+=","; first=false;
      out+="{\"time\":"+IntegerToString((int)t);
      out+=",\"open\":"+Num(iOpen(symbol,tf,shift),digits);
      out+=",\"high\":"+Num(iHigh(symbol,tf,shift),digits);
      out+=",\"low\":"+Num(iLow(symbol,tf,shift),digits);
      out+=",\"close\":"+Num(iClose(symbol,tf,shift),digits);
      out+=",\"tick_volume\":"+IntegerToString((int)iVolume(symbol,tf,shift))+"}";
   }
   return out+"]";
}

string BuildPayload(int i,int tf){
   string s=BrokerSymbols[i]; int digits=(int)MarketInfo(s,MODE_DIGITS); double avg=Samples[i]>0?SpreadSum[i]/Samples[i]:0;
   string p="{\"schema_version\":\"2.0\",\"source\":\"mt4\"";
   p+=",\"symbol\":\""+CanonicalSymbols[i]+"\",\"broker_symbol\":\""+JsonEscape(s)+"\"";
   p+=",\"timeframe\":\""+TfName(tf)+"\",\"sent_at\":"+IntegerToString((int)TimeCurrent());
   p+=",\"broker\":\""+JsonEscape(AccountCompany())+"\",\"account\":\""+IntegerToString(AccountNumber())+"\"";
   p+=",\"candles\":"+BuildCandles(s,tf)+",\"ticks\":{";
   p+="\"samples\":"+IntegerToString((int)Samples[i])+",\"up_ticks\":"+IntegerToString((int)UpTicks[i]);
   p+=",\"down_ticks\":"+IntegerToString((int)DownTicks[i])+",\"unchanged_ticks\":"+IntegerToString((int)FlatTicks[i]);
   p+=",\"delta\":"+IntegerToString((int)(UpTicks[i]-DownTicks[i]))+",\"spread_avg_points\":"+Num(avg,1);
   p+=",\"spread_max_points\":"+Num(SpreadMax[i],1)+",\"last_bid\":"+Num(MarketInfo(s,MODE_BID),digits);
   p+=",\"last_ask\":"+Num(MarketInfo(s,MODE_ASK),digits)+"}}";
   return p;
}

bool PostJson(string payload){
   char data[],response[]; string responseHeaders; StringToCharArray(payload,data,0,WHOLE_ARRAY,CP_UTF8); ArrayResize(data,ArraySize(data)-1);
   string headers="Content-Type: application/json\r\nX-FXPilot-MT4-Token: "+ApiToken+"\r\n";
   ResetLastError(); int code=WebRequest("POST",ServerUrl,headers,HttpTimeoutMs,data,response,responseHeaders);
   if(code==-1){
      int errorCode=GetLastError();
      LastErrorDetail="WebRequest error "+IntegerToString(errorCode);
      LastStatus=LastErrorDetail;
      Print("FXPilot: ",LastErrorDetail," URL=",ServerUrl);
      return false;
   }
   if(code<200 || code>=300){
      LastErrorDetail="HTTP "+IntegerToString(code);
      LastStatus=LastErrorDetail;
      Print("FXPilot: ",LastErrorDetail," response=",CharArrayToString(response));
      return false;
   }
   return true;
}

void SetLabel(string name,string value,int x,int y,int size,color textColor){
   if(ObjectFind(0,name)<0)ObjectCreate(0,name,OBJ_LABEL,0,0,0);
   ObjectSetInteger(0,name,OBJPROP_CORNER,CORNER_LEFT_UPPER);
   ObjectSetInteger(0,name,OBJPROP_XDISTANCE,x);
   ObjectSetInteger(0,name,OBJPROP_YDISTANCE,y);
   ObjectSetInteger(0,name,OBJPROP_FONTSIZE,size);
   ObjectSetInteger(0,name,OBJPROP_COLOR,textColor);
   ObjectSetString(0,name,OBJPROP_FONT,"Arial");
   ObjectSetString(0,name,OBJPROP_TEXT,value);
}

void CreatePanel(){
   ObjectDelete(0,PANEL_BG);
   ObjectCreate(0,PANEL_BG,OBJ_RECTANGLE_LABEL,0,0,0);
   ObjectSetInteger(0,PANEL_BG,OBJPROP_CORNER,CORNER_LEFT_UPPER);
   ObjectSetInteger(0,PANEL_BG,OBJPROP_XDISTANCE,12);
   ObjectSetInteger(0,PANEL_BG,OBJPROP_YDISTANCE,18);
   ObjectSetInteger(0,PANEL_BG,OBJPROP_XSIZE,470);
   ObjectSetInteger(0,PANEL_BG,OBJPROP_YSIZE,238);
   ObjectSetInteger(0,PANEL_BG,OBJPROP_BGCOLOR,C'18,24,34');
   ObjectSetInteger(0,PANEL_BG,OBJPROP_BORDER_COLOR,C'55,75,95');
   ObjectSetInteger(0,PANEL_BG,OBJPROP_BACK,false);
   ObjectSetInteger(0,PANEL_BG,OBJPROP_SELECTABLE,false);
   SetLabel(PANEL_TITLE,"FXPILOT  |  MT4 BRIDGE v2.13",28,30,12,C'70,210,255');
   SetLabel("FXPILOT_TFS","ONE CHART  |  M15  H1  H4  D1  W1",28,54,9,C'170,185,200');
   SetLabel(PANEL_STATUS,"Status: starting",28,78,10,clrWhite);
   for(int i=0;i<SYMBOL_COUNT;i++)SetLabel("FXPILOT_SYMBOL_"+IntegerToString(i),CanonicalSymbols[i],28,106+i*22,10,clrSilver);
   SetLabel(PANEL_COUNTERS,"Packets: 0 OK / 0 failed",28,198,9,C'170,185,200');
   SetLabel(PANEL_NEXT,"Next send: now",28,218,9,C'170,185,200');
}

void UpdatePanel(){
   string next=LastSendAt>0?TimeToString(LastSendAt+SendEverySeconds,TIME_SECONDS):"now";
   color statusColor=(FailedPackets==0?C'90,220,130':C'255,180,70');
   SetLabel(PANEL_STATUS,"Status: "+LastStatus,28,78,10,statusColor);
   for(int i=0;i<SYMBOL_COUNT;i++){
      double bid=MarketInfo(BrokerSymbols[i],MODE_BID);
      string quote=bid>0?DoubleToString(bid,(int)MarketInfo(BrokerSymbols[i],MODE_DIGITS)):"NO QUOTE";
      string line=CanonicalSymbols[i]+"  ["+BrokerSymbols[i]+"]   "+quote+"   samples "+IntegerToString((int)Samples[i]);
      SetLabel("FXPILOT_SYMBOL_"+IntegerToString(i),line,28,106+i*22,10,bid>0?C'100,230,150':C'255,100,100');
   }
   SetLabel(PANEL_COUNTERS,"Packets: "+IntegerToString(OkPackets)+" OK / "+IntegerToString(FailedPackets)+" failed",28,198,9,C'170,185,200');
   SetLabel(PANEL_NEXT,"Next send: "+next,28,218,9,C'170,185,200');
   ChartRedraw();
}

void SendAll(){
   if(StringLen(ApiToken)==0){LastStatus="Set ApiToken in EA settings";UpdatePanel();return;}
   int ok=0,failed=0; LastStatus="Sending...";UpdatePanel();
   for(int i=0;i<SYMBOL_COUNT;i++)for(int t=0;t<TF_COUNT;t++){ if(PostJson(BuildPayload(i,Timeframes[t]))){ok++;OkPackets++;}else{failed++;FailedPackets++;} }
   LastSendAt=TimeCurrent(); LastStatus="Cycle OK "+IntegerToString(ok)+", failed "+IntegerToString(failed);
   if(failed>0 && StringLen(LastErrorDetail)>0)LastStatus+=" | "+LastErrorDetail;
   for(int s=0;s<SYMBOL_COUNT;s++)ResetStats(s); UpdatePanel();
}

void CreateButton(){ ObjectDelete(0,BTN_NAME); ObjectCreate(0,BTN_NAME,OBJ_BUTTON,0,0,0); ObjectSetInteger(0,BTN_NAME,OBJPROP_CORNER,CORNER_LEFT_UPPER); ObjectSetInteger(0,BTN_NAME,OBJPROP_XDISTANCE,338); ObjectSetInteger(0,BTN_NAME,OBJPROP_YDISTANCE,214); ObjectSetInteger(0,BTN_NAME,OBJPROP_XSIZE,132); ObjectSetInteger(0,BTN_NAME,OBJPROP_YSIZE,28); ObjectSetInteger(0,BTN_NAME,OBJPROP_BGCOLOR,C'25,115,165'); ObjectSetInteger(0,BTN_NAME,OBJPROP_COLOR,clrWhite); ObjectSetString(0,BTN_NAME,OBJPROP_TEXT,"SEND NOW"); }

int OnInit(){
   string parsed[]; int count=StringSplit(BrokerSymbolsCsv,',',parsed);
   ResolveBrokerSymbols(parsed,count);
   for(int i=0;i<SYMBOL_COUNT;i++){
      if(!SymbolSelect(BrokerSymbols[i],true))Print("FXPilot: check broker symbol ",BrokerSymbols[i]);
      ResetStats(i);
   }
   CreatePanel();CreateButton();EventSetTimer(1);SampleSymbols();
   LastStatus=(count==SYMBOL_COUNT?"Ready":"Using default symbol names");UpdatePanel();
   if(SendOnStart)SendAll();return INIT_SUCCEEDED;
}
void OnDeinit(const int reason){
   EventKillTimer();
   string names[]={BTN_NAME,PANEL_BG,PANEL_TITLE,"FXPILOT_TFS",PANEL_STATUS,PANEL_COUNTERS,PANEL_NEXT,
                   "FXPILOT_SYMBOL_0","FXPILOT_SYMBOL_1","FXPILOT_SYMBOL_2","FXPILOT_SYMBOL_3"};
   for(int i=0;i<ArraySize(names);i++)ObjectDelete(0,names[i]);
   Comment("");
}
void OnTimer(){SampleSymbols();if(LastSendAt==0 || TimeCurrent()-LastSendAt>=SendEverySeconds)SendAll();else UpdatePanel();}
void OnChartEvent(const int id,const long &lparam,const double &dparam,const string &sparam){if(id==CHARTEVENT_OBJECT_CLICK && sparam==BTN_NAME){ObjectSetInteger(0,BTN_NAME,OBJPROP_STATE,false);SendAll();}}
