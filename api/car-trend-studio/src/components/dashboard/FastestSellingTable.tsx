import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
export const FastestSellingTable = () => {
  const fastestSelling = [
    {
      makeModel: "Skoda Octavia",
      year: "2020",
      avgDaysOnMarket: "6.8",
      avgPrice: "21,902",
      demandLevel: "HIGH"
    },
    {
      makeModel: "Toyota Yaris",
      year: "2019",
      avgDaysOnMarket: "7.2",
      avgPrice: "18,450",
      demandLevel: "HIGH"
    },
    {
      makeModel: "BMW X1",
      year: "2021",
      avgDaysOnMarket: "8.1",
      avgPrice: "35,200",
      demandLevel: "MEDIUM"
    },
    {
      makeModel: "Volkswagen Polo",
      year: "2020",
      avgDaysOnMarket: "9.3",
      avgPrice: "16,800",
      demandLevel: "MEDIUM"
    },
    {
      makeModel: "Renault Clio",
      year: "2019",
      avgDaysOnMarket: "10.5",
      avgPrice: "15,200",
      demandLevel: "MEDIUM"
    },
    {
      makeModel: "Audi A3",
      year: "2020",
      avgDaysOnMarket: "11.2",
      avgPrice: "28,900",
      demandLevel: "LOW"
    },
    {
      makeModel: "Ford Focus",
      year: "2018",
      avgDaysOnMarket: "12.7",
      avgPrice: "14,500",
      demandLevel: "LOW"
    },
    {
      makeModel: "Mercedes A-Class",
      year: "2021",
      avgDaysOnMarket: "13.4",
      avgPrice: "32,700",
      demandLevel: "LOW"
    }
  ];
  return <Card>
      <CardHeader>
        <CardTitle>Fastest Selling Models</CardTitle>
        
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>MAKE/MODEL</TableHead>
              <TableHead>YEAR</TableHead>
              <TableHead>AVG DAYS ON MARKET</TableHead>
              <TableHead>AVG PRICE (KM)</TableHead>
              <TableHead>DEMAND LEVEL</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {fastestSelling.map((item, index) => <TableRow key={index} className="cursor-pointer hover:bg-muted/50">
                <TableCell className="font-medium">{item.makeModel}</TableCell>
                <TableCell>{item.year}</TableCell>
                <TableCell>{item.avgDaysOnMarket}</TableCell>
                <TableCell>{item.avgPrice}</TableCell>
                <TableCell>
                  <Badge 
                    variant={item.demandLevel === "HIGH" ? "default" : item.demandLevel === "MEDIUM" ? "secondary" : "outline"} 
                    className={
                      item.demandLevel === "HIGH" ? "bg-success text-success-foreground" :
                      item.demandLevel === "MEDIUM" ? "bg-warning text-warning-foreground" :
                      "bg-destructive text-destructive-foreground"
                    }
                  >
                    {item.demandLevel}
                  </Badge>
                </TableCell>
              </TableRow>)}
          </TableBody>
        </Table>
      </CardContent>
    </Card>;
};