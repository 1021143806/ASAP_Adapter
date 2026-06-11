对接 Angel 协议

这是风淋门的对接协议，要求按照这个协议对风淋门进行控制

设备需要在风淋门中进入门中时等待风淋完成

## AB自动门接口说明文档.dock

```Shell
自动门接口
1接口清单
序号	接口名称	接口描述	发起方	接收方
1	自动门控制接口	见接口说明	任意	HELI-ACS
2				
3				
				
注意：ACS作为Server
2接口说明
2.1ACS自动门控制接口
接口说明
请求URL	http://ip:port/acs/door/{DOOR01}
请求协议	HTTP
请求方式	POST
内容类型	application/json

请求参数
参数名称	类型（长度）	描述	是否必填	默认值	取值说明
doorSerial	String	自动门序列号	必须	“”	自动门的序列号，即界面中自动门名称，与URL中DOOR01对应
command	String	自动门控制	必须	“0”	通过此参数控制自动门，
“0”：无动作
“1”：打开自动门
“2”：关闭自动门
Direction	string	机器人进入AB门的方向			“1”：进，“2”：出
RobotName	string	机器人编号			
响应参数
参数名称	类型(长度)	描述	是否必填	默认值	取值说明
doorSerial	String	自动门序列号	必须	“”	自动门的序列号，即界面中自动门名称，与URL中DOOR01对应
doorStatus	String	自动门系统状态	必须	“0”	自动门系统状态
“0”：门已关闭
“1”：门完全打开到位           command和doorStatus同时为1，AGV才能驶离自动门
“2”：故障
command	String	此参数和post发送过来的command数据一样	必须	“0”	“0”：无动作
“1”：打开自动门；              command和doorStatus同时为1，AGV才能驶离自动门
“2”：关闭自动门
code	String	响应状态码	必须	“200”	“200”：正常
“500”：异常

样例
请求参数	
响应样例
成功	
失败	
	




2.2获取自动门状态接口
接口说明
请求URL	http://ip:port/acs/door/{DOOR01}
请求协议	HTTP
请求方式	GET
内容类型	application/json
响应参数
参数名称	类型(长度)	描述	是否必填	默认值	取值说明
doorSerial	String	自动门序列号	必须	“”	自动门的序列号，即界面中自动门名称，与URL中DOOR01对应
doorStatus	String	自动门系统状态	必须	“0”	自动门系统状态
“0”：未开到位
“1”：门完全打开到位
“2”：故障
command	String	此参数和post发送过来的command数据一样	必须	“0”	“0”：无动作
“1”：打开自动门          command和doorStatus同时为1，AGV才能驶离自动门
“2”：关闭自动门
code	String	响应状态码	必须	“200”	“200”：正常
“500”：异常

样例
响应样例
成功	
失败	
	

注：1、当前AB安全门有合力AGV通过，所以，需要KIVA 跟合力交管部分需要做交互区，同一时刻只能有一个辆车通行。
2、当两个方向【进出两个方向】同时有车辆进入的时候，只能一车辆申请。
```




## 区域管控*protocol*20251113.txt

这是区域管控的第三方协议，要求进入区域时要请求并占用，只有区域内没车时才能进入

```Shell
# 1. 请求进入区域（独占式，该区域A方占用后，其它方不允许进入，许可后未调用退出区域之前，A方多个车辆均可进入）
POST /api/zones/enter HTTP/1.1
Content-Type: application/json

{
  "zone_id": "zone_001",
  "client_id": "client_a"
}

# 响应（成功）：
HTTP/1.1 200 OK
Content-Type: application/json

{
  "permission_id": "perm_123456",
  "zone_id": "zone_001", 
  "client_id": "client_a",
  "status": "granted"
}

# 响应（区域被占用）：
HTTP/1.1 409 Conflict
Content-Type: application/json

{
  "error": "Zone is currently occupied",
  "occupied_by": "client_b"
}



# 2. 退出区域（许可方车辆全部出该区域后，调用释放，调用失败后重试）
POST /api/zones/exit HTTP/1.1
Content-Type: application/json

{
  "zone_id": "zone_001",
  "client_id": "client_a"
}

# 响应：
HTTP/1.1 200 OK
Content-Type: application/json

{
  "zone_id": "zone_001",
  "client_id": "client_a", 
  "status": "released"
}



# 3. 查询区域状态
GET /api/zones/status?zone_id=zone_001 HTTP/1.1

# 响应：
HTTP/1.1 200 OK
Content-Type: application/json

{
  "zone_id": "zone_001",
  "status": "occupied",     // or "available"
  "occupied_by": "client_b"
}

```




